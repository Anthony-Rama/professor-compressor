import asyncio
import time
from unittest.mock import AsyncMock, Mock, patch

from professor_compressor.domain import JobState, UploadJob
from professor_compressor.relay import upload as upload_relay
from professor_compressor.web.session_routes import (
    RESUME_COOKIE_NAME,
    browser_cookie_matches,
)
from tests.support.runtime import DiagnosticsTestCase, RelayTestCase, WebAppTestCase


class SessionTests(RelayTestCase):
    async def test_head_does_not_claim_or_extend_link(self):
        job = self.job(JobState.OPEN)
        expiry = job.expires_at
        with patch.object(self.runtime.alerts, "schedule") as alert:
            response = await self.client.head("/upload/audit")
            self.assertEqual(response.status, 200)
            self.assertIs(job.state, JobState.OPEN)
            self.assertEqual(job.expires_at, expiry)
            alert.assert_not_called()
            response = await self.client.get("/upload/audit")
            self.assertIn('id="submit"', await response.text())
            self.assertIs(job.state, JobState.CLAIMED)
            self.assertIn("link opened", alert.call_args.args[0])

    async def test_expiry_reports_last_confirmed_stage_once(self):
        job = self.job(JobState.OPEN)
        with patch.object(self.runtime.alerts, "schedule") as alert:
            job.expires_at = time.time() - 1
            self.assertIsNone(self.runtime.sessions.active_job(job.token))
            self.runtime.sessions.discard_expired_jobs()
            self.assertEqual(alert.call_count, 1)
            self.assertIn("never opened", alert.call_args.args[0])

            job = self.job()
            job.selected_count = 2
            job.compression_started = True
            job.expires_at = time.time() - 1
            self.runtime.sessions.discard_expired_jobs()
            self.assertEqual(alert.call_count, 2)
            self.assertIn("Compress was pressed", alert.call_args.args[0])

            job = self.job()
            job.browser_failure_reported = True
            job.expires_at = time.time() - 1
            self.runtime.sessions.discard_expired_jobs()
            self.assertEqual(alert.call_count, 2)

    async def test_pending_request_survives_http_wait_and_expiry(self):
        job = self.job()
        gate = asyncio.Event()
        started = asyncio.Event()

        async def deliver(*args):
            started.set()
            await gate.wait()
            return "delivered once"

        with (
            patch.object(upload_relay, "DELIVERY_RESPONSE_WAIT_SECONDS", 0.01),
            patch.object(
                self.runtime.delivery,
                "deliver_browser_results",
                AsyncMock(side_effect=deliver),
            ) as sender,
            patch.object(self.runtime.alerts, "schedule") as alert,
        ):
            worker = asyncio.create_task(self.runtime.delivery.run_worker(1))
            try:
                response = await self.post_video()
                self.assertEqual(response.status, 202)
                self.assertTrue(
                    any(
                        "Relay upload started" in call.args[0]
                        for call in alert.call_args_list
                    )
                )
                await started.wait()
                job.expires_at = time.time() - 1
                self.runtime.sessions.discard_expired_jobs()
                self.assertIn(job.token, self.runtime.sessions.jobs)
                second = await self.post_video()
                self.assertEqual(second.status, 202)
                gate.set()
                await asyncio.wait_for(self.runtime.relay.queue.join(), 2)
                status = await self.client.get(
                    "/upload/audit/status", headers={"X-Upload-Session": "private"}
                )
                self.assertEqual(status.status, 200)
                self.assertTrue((await status.json())["ok"])
                duplicate = await self.post_video()
                self.assertEqual(duplicate.status, 200)
                sender.assert_awaited_once()
                self.assertEqual(self.runtime.relay.buffered_bytes, 0)
                self.assertEqual(self.runtime.relay.active_uploads, 0)
                self.assertNotIn(job.token, self.runtime.sessions.jobs)
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)

    async def test_receipts_expire_and_never_retain_media(self):
        job = self.job()
        self.runtime.sessions.record_outcome(
            job, 200, {"ok": True, "message": "sent", "count": 1}
        )
        self.runtime.sessions.jobs.clear()
        receipt = self.runtime.sessions.outcomes[job.token]
        self.assertFalse(hasattr(receipt, "results"))
        self.assertNotIn("private", repr(receipt))
        with patch.object(time, "time", return_value=receipt.expires_at + 1):
            self.runtime.sessions.discard_expired_jobs()
            self.assertFalse(self.runtime.sessions.outcomes)

    async def test_heartbeat_requires_secret_and_cannot_revive_expired_job(self):
        job = self.job()
        forbidden = await self.client.post("/upload/audit/heartbeat")
        self.assertEqual(forbidden.status, 403)
        response = await self.client.post(
            "/upload/audit/heartbeat", headers={"X-Upload-Session": "private"}
        )
        self.assertEqual(response.status, 200)
        self.assertGreater(job.processing_until, time.time())
        await self.client.post(
            "/upload/audit/pause", headers={"X-Upload-Session": "private"}
        )
        self.assertEqual(job.processing_until, 0)
        job.claimed_at = time.time() - 7190
        await self.client.post(
            "/upload/audit/heartbeat", headers={"X-Upload-Session": "private"}
        )
        self.assertLessEqual(job.expires_at, job.claimed_at + 7200)
        job.expires_at = time.time() - 1
        expired = await self.client.post(
            "/upload/audit/heartbeat", headers={"X-Upload-Session": "private"}
        )
        self.assertEqual(expired.status, 410)


class SessionPageTests(WebAppTestCase):
    async def test_refresh_requires_browser_cookie_and_revokes_old_page(self) -> None:
        token = "refresh-test"
        job = UploadJob(
            token=token,
            user_id=123,
            channel_id=456,
            expires_at=9999999999,
            discord_limit=20_000_000,
            interaction=Mock(),
        )
        self.runtime.sessions.jobs[token] = job
        head = await self.client.head(f"/upload/{token}")
        self.assertNotIn("Set-Cookie", head.headers)
        self.assertIs(job.state, JobState.OPEN)

        first = await self.client.get(f"/upload/{token}")
        cookie = first.cookies["__Secure-pc-browser"]
        self.assertTrue(cookie["secure"])
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(cookie["path"], f"/upload/{token}")
        first_secret = job.claim_secret
        self.assertIn("Choose videos", await first.text())

        stranger = await self.client.get(f"/upload/{token}")
        self.assertIn("Private link unavailable", await stranger.text())
        self.assertEqual(job.claim_secret, first_secret)
        wrong_cookie = await self.client.get(
            f"/upload/{token}",
            headers={"Cookie": "__Secure-pc-browser=incorrect"},
        )
        self.assertIn("Private link unavailable", await wrong_cookie.text())

        authorized = await self.client.get(
            f"/upload/{token}",
            headers={"Cookie": f"__Secure-pc-browser={cookie.value}"},
        )
        self.assertIn("Page refreshed", await authorized.text())
        self.assertNotEqual(job.claim_secret, first_secret)
        stale = await self.client.get(
            f"/upload/{token}/status",
            headers={"X-Upload-Session": first_secret},
        )
        self.assertEqual(stale.status, 403)
        current = await self.client.get(
            f"/upload/{token}/status",
            headers={"X-Upload-Session": job.claim_secret},
        )
        self.assertTrue((await current.json())["ready"])

    async def test_refresh_during_delivery_only_shows_status(self) -> None:
        token = "delivery-refresh"
        job = UploadJob(
            token=token,
            user_id=123,
            channel_id=456,
            expires_at=9999999999,
            discord_limit=20_000_000,
            interaction=Mock(),
            state=JobState.QUEUED,
            claim_secret="delivery-secret",
            browser_secret="browser-secret",
        )
        self.runtime.sessions.jobs[token] = job
        response = await self.client.get(
            f"/upload/{token}",
            headers={"Cookie": "__Secure-pc-browser=browser-secret"},
        )
        document = await response.text()
        self.assertIn("Checking Discord delivery", document)
        self.assertNotIn('id="clips"', document)
        self.assertEqual(job.claim_secret, "delivery-secret")

        without_cookie = await self.client.get(f"/upload/{token}")
        self.assertIn("Private link unavailable", await without_cookie.text())

    async def test_expired_claimed_link_does_not_reopen_with_cookie(self) -> None:
        token = "expired-refresh"
        self.runtime.sessions.jobs[token] = UploadJob(
            token=token,
            user_id=123,
            channel_id=456,
            expires_at=1,
            discord_limit=20_000_000,
            interaction=Mock(),
            state=JobState.CLAIMED,
            claim_secret="old-secret",
            browser_secret="browser-secret",
        )
        response = await self.client.get(
            f"/upload/{token}",
            headers={"Cookie": "__Secure-pc-browser=browser-secret"},
        )
        self.assertIn("Private link unavailable", await response.text())
        self.assertNotIn(token, self.runtime.sessions.jobs)


class BrowserCookieTests(DiagnosticsTestCase):
    async def test_non_ascii_cookie_is_rejected_without_server_error(self):
        self.job.browser_secret = "expected-cookie"
        request = Mock(cookies={RESUME_COOKIE_NAME: "é"})
        self.assertFalse(browser_cookie_matches(request, self.job.browser_secret))
