import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2] / "config/windmill/source"
with patch.object(sys, "path", [str(SOURCE), *sys.path]):
    import offmenu_release_journal as journal
    from scoped_reviewed_release import Release

ONE = "12345678-1234-1234-1234-123456789abc"
TWO = "12345678-1234-1234-1234-123456789def"


class ReleaseJournalTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.pins = Release(*(letter * 64 for letter in "abcd"))
        self.effect = self.path / "fixture-canonical-receipt"

    def adapter(self, dispatch):
        return journal.bind_offmenu_original(
            dispatch, lambda _original, _job: "installed" if self.effect.exists() else "not_started")

    def invoke(self, adapter, job, pins=None):
        pins = pins or self.pins
        with patch.object(journal, "_open_protected",
                          lambda _path: os.open(self.path, os.O_RDONLY | os.O_DIRECTORY)):
            return adapter(pins.operation_id, job, pins)

    def test_second_instance_new_windmill_run_only_reads_original(self):
        def dispatch(operation, job):
            self.assertEqual((self.pins.operation_id, ONE), (operation, job))
            self.assertTrue((self.path / journal.INTENT).is_file())
            self.effect.touch(exist_ok=False)
        self.assertEqual("installed", self.invoke(self.adapter(dispatch), ONE))
        second = self.adapter(lambda *_: self.fail("duplicate dispatch"))
        self.assertEqual("installed", self.invoke(second, TWO))
        with self.assertRaisesRegex(RuntimeError, "MISMATCH"):
            self.invoke(second, TWO, self.pins._replace(package_sha256="e"*64))

    def test_interruption_refuses_retry_without_canonical_receipt(self):
        def interrupted(*_):
            raise OSError("interrupted before original acknowledgement")
        with self.assertRaises(OSError):
            self.invoke(self.adapter(interrupted), ONE)
        with self.assertRaisesRegex(RuntimeError, "RECONCILIATION"):
            self.invoke(self.adapter(lambda *_: self.fail("replay")), TWO)

    def test_no_handoff_does_not_replay_preexisting_original_effect(self):
        self.effect.touch()
        with self.assertRaisesRegex(RuntimeError, "EXISTS_OR_UNKNOWN"):
            self.invoke(self.adapter(lambda *_: self.fail("replay")), ONE)
        self.assertFalse((self.path / journal.INTENT).exists())
