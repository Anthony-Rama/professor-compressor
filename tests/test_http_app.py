import asyncio
import unittest
from unittest.mock import Mock, patch

from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

from professor_compressor.application import create_web_application, jobs, request_times
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
        self.client = TestClient(TestServer(create_web_application()))
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        jobs.clear()
        request_times.clear()
        await self.client.close()

    async def test_public_routes_and_security_headers(self) -> None:
        expected_types = {
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
        self.assertTrue(received[0].results[0].name.endswith(".mp4"))
        self.assertNotIn("/", received[0].results[0].name)
        self.assertIs(job.state, JobState.DONE)

    async def test_packaged_browser_assets_are_available(self) -> None:
        expected_types = {
            "/brand/professor-compressor.png": "image/png",
            "/assets/ffmpeg-esm/index.js": "application/javascript",
            "/assets/util-esm/index.js": "application/javascript",
            "/assets/core-esm/ffmpeg-core.wasm": "application/wasm",
            "/assets/core-mt-esm/ffmpeg-core.worker.js": "application/javascript",
        }
        for path, content_type in expected_types.items():
            with self.subTest(path=path):
                response = await self.client.head(path)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.content_type, content_type)


if __name__ == "__main__":
    unittest.main()
