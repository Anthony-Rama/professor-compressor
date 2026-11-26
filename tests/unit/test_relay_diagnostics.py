import json
import unittest

from professor_compressor.relay.diagnostics import (
    parse_compression_diagnostics,
    sanitize_telemetry,
)


class RelayDiagnosticsTests(unittest.TestCase):
    def test_telemetry_rejects_sensitive_and_unbounded_fields(self):
        self.assertEqual(
            sanitize_telemetry(
                {
                    "caption": "private",
                    "filename": "private.mp4",
                    "url": "secret",
                    "path": "compressed",
                    "engine": "multithreaded",
                    "encoding_attempts": 2,
                    "engine_seconds": float("nan"),
                    "input_width": 10**1000,
                    "output_fps": True,
                    "hidden_seconds": -1,
                    "batch_id": "a" * 32,
                }
            ),
            {
                "path": "compressed",
                "engine": "multithreaded",
                "encoding_attempts": 2,
                "batch_id": "a" * 32,
            },
        )
        self.assertEqual(sanitize_telemetry([]), {})

    def test_invalid_browser_diagnostics_are_ignored(self) -> None:
        self.assertEqual(parse_compression_diagnostics(b"not json", max_clips=10), [])
        self.assertEqual(parse_compression_diagnostics(b"[]", max_clips=10), [])
        self.assertEqual(
            parse_compression_diagnostics(b'[{"copied":true}]', max_clips=10), []
        )
        self.assertEqual(
            parse_compression_diagnostics(
                b'[{"input_bytes":10,"duration_seconds":NaN,"video_kbps":100,"copied":false}]',
                max_clips=10,
            ),
            [],
        )
        self.assertEqual(
            parse_compression_diagnostics(
                b'[{"input_bytes":10,"duration_seconds":1.5,"video_kbps":null,"copied":false}]',
                max_clips=10,
            )[0].video_kbps,
            None,
        )
        self.assertEqual(
            parse_compression_diagnostics(
                b'[{"input_bytes":10,"duration_seconds":null,"video_kbps":null,"copied":true,"input_format":"mov"}]',
                max_clips=10,
            )[0].input_format,
            "mov",
        )
        self.assertEqual(
            parse_compression_diagnostics(
                b'[{"input_bytes":10,"duration_seconds":null,"video_kbps":null,"copied":true,"input_format":"secret.mp4"}]',
                max_clips=10,
            ),
            [],
        )

    def test_relay_diagnostics_honor_the_configured_batch_limit(self) -> None:
        raw = json.dumps([{"input_bytes": 10, "copied": True}] * 2).encode()
        self.assertEqual(parse_compression_diagnostics(raw, max_clips=1), [])
        self.assertEqual(len(parse_compression_diagnostics(raw, max_clips=2)), 2)
