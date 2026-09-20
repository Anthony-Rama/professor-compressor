import unittest
from unittest.mock import patch

from metrics import RuntimeMetrics


class RuntimeMetricsTests(unittest.TestCase):
    @patch("metrics.time.monotonic", side_effect=[100.0, 112.9])
    def test_snapshot_contains_aggregate_counters_and_uptime(self, _clock) -> None:
        metrics = RuntimeMetrics()
        metrics.increment("sessions_created")
        metrics.increment("files_delivered", 3)
        metrics.increment("bytes_delivered", 1024)

        snapshot = metrics.snapshot()

        self.assertEqual(snapshot["uptime_seconds"], 12)
        self.assertEqual(snapshot["sessions_created"], 1)
        self.assertEqual(snapshot["files_delivered"], 3)
        self.assertEqual(snapshot["bytes_delivered"], 1024)
        self.assertEqual(snapshot["deliveries_failed"], 0)

    def test_negative_increment_is_rejected(self) -> None:
        metrics = RuntimeMetrics()
        with self.assertRaises(ValueError):
            metrics.increment("files_delivered", -1)


if __name__ == "__main__":
    unittest.main()
