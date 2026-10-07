import asyncio
import json
import re
import unittest
from unittest.mock import Mock, patch
from xml.etree import ElementTree

from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

from professor_compressor.application import (
    PUBLIC_BASE_URL,
    create_web_application,
    jobs,
    request_times,
)
from professor_compressor.domain import JobState, UploadJob


def box(kind: bytes, payload: bytes = b"") -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


def valid_test_mp4() -> bytes:
    return (
        box(b"ftyp", b"isom" + b"\x00\x00\x02\x00" + b"iso2mp41")
        + box(b"moov", b"metadata")
        + box(b"mdat", b"video-data")
    )


class HttpApplicationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        import professor_compressor.application as application

        for name in ("delivery_bytes_held", "active_relay_uploads"):
            patcher = patch.object(application, name, 0)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(TestServer(create_web_application()))
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        jobs.clear()
        request_times.clear()
        await self.client.close()

    async def test_public_routes_and_security_headers(self) -> None:
        expected_types = {
            "/": "text/html",
            "/guide/compress-video-for-discord": "text/html",
            "/robots.txt": "text/plain",
            "/sitemap.xml": "application/xml",
            "/googlec910ef324dae35ac.html": "text/html",
            "/healthz": "application/json",
            "/metricsz": "application/json",
            "/privacy": "text/html",
            "/terms": "text/html",
            "/upload/not-a-real-token": "text/html",
        }
        for path, content_type in expected_types.items():
            with self.subTest(path=path):
                response = await self.client.get(path)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.content_type, content_type)
                self.assertEqual(
                    response.headers["Cross-Origin-Embedder-Policy"],
                    "require-corp",
                )

    async def test_public_pages_are_indexable_and_session_pages_are_not(self) -> None:
        home = await self.client.get("/")
        home_html = await home.text()
        self.assertEqual(home.status, 200)
        self.assertNotIn("X-Robots-Tag", home.headers)
        self.assertIn(
            "<title>Professor Compressor | Discord Video Compressor Bot</title>",
            home_html,
        )
        self.assertIn(
            '<link rel="icon" type="image/png" href="/brand/professor-compressor.png">',
            home_html,
        )
        self.assertIn('rel="canonical"', home_html)
        self.assertIn('type="application/ld+json"', home_html)
        home_schema = json.loads(
            re.search(
                r'<script type="application/ld\+json">(.*?)</script>', home_html
            ).group(1)
        )
        self.assertEqual(home_schema["@graph"][0]["@type"], "WebSite")
        self.assertEqual(
            home_schema["@graph"][0]["url"],
            f"{PUBLIC_BASE_URL}/",
        )
        self.assertIn("Add to Discord", home_html)
        self.assertIn("Written Guide", home_html)
        self.assertIn("Read how it works", home_html)
        self.assertIn('id="support"', home_html)
        self.assertIn("Join the support server", home_html)
        self.assertIn(
            'href="mailto:professorcompressor.support@gmail.com">Support and contact',
            home_html,
        )
        self.assertIn(
            'href="https://discord.com/invite/32RWwNWyEH">Support server',
            home_html,
        )
        self.assertNotIn("Video guide", home_html)
        self.assertNotIn("Top.gg", home_html)
        self.assertIn('id="listings"', home_html)
        self.assertIn("https://dsc.sh/bot/1550752400271351839", home_html)
        self.assertIn("https://discordbotlist.com/bots/professor-compressor", home_html)
        self.assertIn("https://discord.bots.gg/bots/1550752400271351839", home_html)
        self.assertIn("may be sent unchanged", home_html)

        guide = await self.client.get("/guide/compress-video-for-discord")
        self.assertEqual(guide.status, 200)
        self.assertNotIn("X-Robots-Tag", guide.headers)
        guide_html = await guide.text()
        self.assertIn("How to compress a video", guide_html)
        self.assertIn("When something does not work", guide_html)
        self.assertIn("The private link says it expired", guide_html)
        self.assertIn(
            "<title>Professor Compressor | How to Compress Videos for Discord</title>",
            guide_html,
        )

        for path, title in (
            ("/privacy", "Professor Compressor | Privacy Policy"),
            ("/terms", "Professor Compressor | Terms of Service"),
            (
                "/upload/not-a-real-token",
                "Professor Compressor | Private link unavailable",
            ),
        ):
            with self.subTest(path=path):
                response = await self.client.get(path)
                document = await response.text()
                self.assertIn(f"<title>{title}</title>", document)
                self.assertIn('rel="icon" type="image/png"', document)

        robots = await self.client.get("/robots.txt")
        self.assertIn("Sitemap:", await robots.text())
        sitemap = await self.client.get("/sitemap.xml")
        urls = ElementTree.fromstring(await sitemap.text())
        locs = [node.text for node in urls.iter() if node.tag.endswith("loc")]
        self.assertEqual(len(locs), 4)
        self.assertTrue(all("/upload/" not in url for url in locs))

        verification = await self.client.get("/googlec910ef324dae35ac.html")
        self.assertEqual(
            await verification.text(),
            "google-site-verification: googlec910ef324dae35ac.html",
        )

        for path in ("/upload/not-a-real-token", "/healthz", "/metricsz"):
            with self.subTest(path=path):
                response = await self.client.get(path)
                self.assertIn("noindex", response.headers["X-Robots-Tag"])

    async def test_valid_result_batch_reaches_the_delivery_queue(self) -> None:
        import professor_compressor.application as application

        token = "valid-session"
        job = UploadJob(
            token=token,
            user_id=123,
            channel_id=456,
            expires_at=9999999999,
            discord_limit=20_000_000,
            interaction=Mock(),
            state=JobState.CLAIMED,
            claim_secret="claim-secret",
        )
        jobs[token] = job
        queue = asyncio.Queue(maxsize=1)
        received = []

        async def accept_delivery() -> None:
            delivery = await queue.get()
            received.append(delivery)
            delivery.completed.set_result("Delivered in test")
            queue.task_done()

        form = FormData()
        form.add_field("caption", "x" * 1000)
        form.add_field(
            "diagnostics",
            json.dumps(
                [
                    {
                        "input_bytes": 1000,
                        "duration_seconds": 2.5,
                        "video_kbps": 1000,
                        "copied": False,
                        "input_format": "mov",
                    }
                ]
            ),
        )
        form.add_field(
            "clips",
            valid_test_mp4(),
            filename="../clip.mp4",
            content_type="video/mp4",
        )
        consumer = asyncio.create_task(accept_delivery())
        with patch.object(application, "delivery_queue", queue):
            response = await self.client.post(
                f"/upload/{token}",
                data=form,
                headers={"X-Upload-Session": "claim-secret"},
            )
        await consumer

        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["message"], "Delivered in test")
        self.assertEqual(len(received[0].results), 1)
        self.assertEqual(received[0].caption, "x" * 1000)
        self.assertEqual(received[0].diagnostics[0].input_bytes, 1000)
        self.assertEqual(received[0].diagnostics[0].input_format, "mov")
        self.assertTrue(received[0].results[0].name.endswith(".mp4"))
        self.assertNotIn("/", received[0].results[0].name)
        self.assertIs(job.state, JobState.DONE)

    async def test_rejects_oversized_caption_before_delivery(self) -> None:
        import professor_compressor.application as application

        token = "long-caption"
        job = UploadJob(
            token=token,
            user_id=123,
            channel_id=456,
            expires_at=9999999999,
            discord_limit=20_000_000,
            interaction=Mock(),
            state=JobState.CLAIMED,
            claim_secret="claim-secret",
        )
        jobs[token] = job
        queue = asyncio.Queue(maxsize=1)
        form = FormData()
        form.add_field("caption", "x" * 1001)
        form.add_field(
            "clips", valid_test_mp4(), filename="clip.mp4", content_type="video/mp4"
        )
        with patch.object(application, "delivery_queue", queue):
            response = await self.client.post(
                f"/upload/{token}",
                data=form,
                headers={"X-Upload-Session": "claim-secret"},
            )
        self.assertEqual(response.status, 400)
        self.assertIn("1,000 characters", (await response.json())["error"])
        self.assertTrue(queue.empty())
        self.assertIs(job.state, JobState.CLAIMED)

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
        jobs[token] = job
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
        jobs[token] = job
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
        jobs[token] = UploadJob(
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
        self.assertNotIn(token, jobs)

    async def test_packaged_browser_assets_are_available(self) -> None:
        expected_types = {
            "/brand/professor-compressor.png": "image/png",
            "/assets/ffmpeg-esm/index.js": {
                "application/javascript",
                "text/javascript",
            },
            "/assets/util-esm/index.js": {"application/javascript", "text/javascript"},
            "/assets/core-esm/ffmpeg-core.wasm": "application/wasm",
            "/assets/core-mt-esm/ffmpeg-core.worker.js": {
                "application/javascript",
                "text/javascript",
            },
        }
        for path, content_type in expected_types.items():
            with self.subTest(path=path):
                response = await self.client.head(path)
                self.assertEqual(response.status, 200)
                if isinstance(content_type, set):
                    self.assertIn(response.content_type, content_type)
                else:
                    self.assertEqual(response.content_type, content_type)

    async def test_third_party_notices_are_publicly_available(self) -> None:
        for path, expected_text in (
            ("/brand/third-party-notices.txt", "Copyright (c) 2019 Jerome Wu"),
            ("/brand/COPYING.GPLv2", "GNU GENERAL PUBLIC LICENSE"),
        ):
            with self.subTest(path=path):
                response = await self.client.get(path)
                self.assertEqual(response.status, 200)
                self.assertIn(expected_text, await response.text())

    async def test_rejected_uploads_release_resources_and_allow_retry(self) -> None:
        import professor_compressor.application as app

        job = UploadJob(
            token="limits",
            user_id=1,
            channel_id=2,
            expires_at=9999999999,
            discord_limit=1000,
            interaction=Mock(),
            state=JobState.CLAIMED,
            claim_secret="secret",
        )
        jobs[job.token] = job
        for limit, data, expected in (
            (1, valid_test_mp4(), 503),
            (10000, b"invalid", 400),
        ):
            form = FormData()
            form.add_field("clips", data, filename="clip.mp4")
            with (
                patch.object(app, "delivery_queue", asyncio.Queue(1)),
                patch.object(app, "MAX_DELIVERY_BUFFER_BYTES", limit),
            ):
                response = await self.client.post(
                    "/upload/limits", data=form, headers={"X-Upload-Session": "secret"}
                )
            self.assertEqual(response.status, expected)
            self.assertEqual(app.delivery_bytes_held, 0)
            self.assertEqual(app.active_relay_uploads, 0)
            self.assertIs(job.state, JobState.CLAIMED)

    async def test_malformed_secret_and_failure_reports(self) -> None:
        job = UploadJob(
            token="reports",
            user_id=1,
            channel_id=2,
            expires_at=9999999999,
            discord_limit=1000,
            interaction=Mock(),
            state=JobState.CLAIMED,
            claim_secret="secret",
        )
        jobs[job.token] = job
        response = await self.client.post(
            "/upload/reports", headers={"X-Upload-Session": "é"}
        )
        self.assertEqual(response.status, 403)
        for payload, expected in (([], 400), ({"stage": "x" * 5000}, 413)):
            response = await self.client.post(
                "/upload/reports/failure",
                json=payload,
                headers={"X-Upload-Session": "secret"},
            )
            self.assertEqual(response.status, expected)


if __name__ == "__main__":
    unittest.main()
