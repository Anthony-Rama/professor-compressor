"""A started EventStore in a temporary directory, with one session to record."""

import tempfile
import unittest
import uuid
from pathlib import Path

from professor_compressor.observability.store import EventStore
from tests.support.waiting import wait_until


class EventStoreTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = EventStore(
            str(Path(self.directory.name) / "events.sqlite3"), key="test-only" * 8
        )
        self.store.start()
        self.addCleanup(self.store.stop)
        self.assertTrue(wait_until(lambda: self.store.ready, timeout=3))
        self.session = uuid.uuid4().hex
        self.reference = "PC-" + self.session[:12].upper()
        self.run = uuid.uuid4().hex

    def emit(self, event, **kwargs):
        self.store.record(
            self.session, self.reference, event, run_id=self.run, **kwargs
        )

    def view(self, **kwargs):
        self.store._queue.join()
        return self.store.query(reference=self.reference, **kwargs)
