import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Records SSL_CERT_FILE when aiohttp is first imported. aiohttp builds its
# verified SSL context at import time, so a later setting has no effect.
PROBE = textwrap.dedent(
    """
    import os, runpy, sys

    class Watch:
        seen = "never imported"

        def find_spec(self, name, path=None, target=None):
            if name == "aiohttp" and Watch.seen == "never imported":
                Watch.seen = os.environ.get("SSL_CERT_FILE")
            return None

    sys.meta_path.insert(0, Watch())
    try:
        if sys.argv[1] == "module":
            runpy.run_module("professor_compressor", run_name="__main__")
        else:
            import professor_compressor

            professor_compressor.run()
    except BaseException:
        pass  # No token is configured, so startup stops before connecting.
    print(Watch.seen)
    """
)


class EntryPointTests(unittest.TestCase):
    def probe(self, entry: str) -> str:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"SSL_CERT_FILE", "DISCORD_TOKEN"}
        }
        environment["PYTHON_DOTENV_DISABLED"] = "1"
        result = subprocess.run(
            [sys.executable, "-c", PROBE, entry],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        return result.stdout.strip().splitlines()[-1]

    def test_certifi_bundle_is_configured_before_aiohttp_loads(self) -> None:
        import certifi

        for entry in ("package", "module"):
            with self.subTest(entry=entry):
                self.assertEqual(self.probe(entry), certifi.where())
