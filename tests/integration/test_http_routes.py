import json
import re
from xml.etree import ElementTree

from professor_compressor.web.assets import ASSET_PREFIX
from tests.support.runtime import RelayTestCase, WebAppTestCase


class PublicRouteTests(WebAppTestCase):
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
        schema_match = re.search(
            r'<script type="application/ld\+json">(.*?)</script>', home_html
        )
        assert schema_match is not None
        home_schema = json.loads(schema_match.group(1))
        self.assertEqual(home_schema["@graph"][0]["@type"], "WebSite")
        self.assertEqual(
            home_schema["@graph"][0]["url"],
            f"{self.runtime.settings.public_base_url}/",
        )
        self.assertIn("Add to Discord", home_html)
        self.assertIn(
            '<a href="/guide/compress-video-for-discord">Guide</a>', home_html
        )
        self.assertIn('href="#how">See how it works</a>', home_html)
        self.assertIn('<section id="how"', home_html)
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
        self.assertIn('id="listings"', home_html)
        listings = home_html.split('id="listings"', 1)[1].split("</section>", 1)[0]
        self.assertIn(
            '<a href="https://top.gg/bot/1550752400271351839">Top.gg</a>', listings
        )
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
        self.assertTrue(all(url is not None and "/upload/" not in url for url in locs))

        verification = await self.client.get("/googlec910ef324dae35ac.html")
        self.assertEqual(
            await verification.text(),
            "google-site-verification: googlec910ef324dae35ac.html",
        )

        for path in ("/upload/not-a-real-token", "/healthz", "/metricsz"):
            with self.subTest(path=path):
                response = await self.client.get(path)
                self.assertIn("noindex", response.headers["X-Robots-Tag"])

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


class AssetCacheTests(RelayTestCase):
    async def test_only_versioned_assets_are_immutable(self):
        for prefix, immutable in ((ASSET_PREFIX, True), ("/assets", False)):
            response = await self.client.get(
                prefix + "/core-mt-esm/ffmpeg-core.worker.js"
            )
            self.assertEqual(response.status, 200)
            self.assertEqual(
                "immutable" in response.headers.get("Cache-Control", ""), immutable
            )

    async def test_unversioned_browser_modules_revalidate_before_reuse(self):
        # /brand/ URLs carry no version, so a browser must not mix cached and
        # freshly deployed modules that import each other.
        for path in (
            "/brand/compressor.js",
            "/brand/batch-controller.js",
            "/brand/shared.js",
            "/brand/dashboard.css",
        ):
            with self.subTest(path=path):
                response = await self.client.get(path)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers.get("Cache-Control"), "no-cache")
                validator = {"If-None-Match": response.headers["ETag"]}
                revalidated = await self.client.get(path, headers=validator)
                self.assertEqual(revalidated.status, 304)
