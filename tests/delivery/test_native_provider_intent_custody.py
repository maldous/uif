"""Restart and contention at the existing native pre-egress intent writer."""
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from test_native_remote_authority import FENCE, GRANT, ISSUE, NOW, RECORD, ROUTE, make
from uif.effect_authority import Operation, OriginalEffect, TerminalEffect
from uif.handoff_store import Hold
from uif.stokowski_cloudflare.native_composition import NativeComposition
from uif.stokowski_cloudflare.remote_authority import AuthorityUnavailable, ProviderReply
from uif.stokowski_cloudflare.shared_admission import SendIntent


class NativeIntentCustodyTest(TestCase):
    def setUp(self):
        self.path = Path(self.enterContext(TemporaryDirectory()))
        self.journal = self.path / '.git/uif-original-effects'
        self.journal.mkdir(mode=0o700, parents=True)
        self.op = Operation(FENCE, 'd'*64, 'e'*64)
        self.intent = SendIntent(self.op, ROUTE, RECORD.attempt_id, '1'*64, '2'*64,
                                 int(NOW.timestamp()) + 80, 100)
        self.retained = self.journal / (self.op.original_id + '.json')
        self.worker, self.remote = make()
        self.composition()

    def composition(self):
        owner = NativeComposition(self.remote, '/unused')
        owner.installation = lambda: {'assignments':{ISSUE:{'workspace_binding':{'path':str(self.path)}}}}
        owner._running.set((GRANT, RECORD))
        return owner

    def test_oversized_collision_holds_without_unbounded_read_or_egress(self):
        overflow = b'x'*8193
        self.retained.write_bytes(overflow)
        self.retained.chmod(0o600)
        read = Path.read_bytes

        def no_unbounded_read(path):
            if path == self.retained:
                raise AssertionError('unbounded original intent read')
            return read(path)

        with patch.object(Path, 'read_bytes', no_unbounded_read):
            with self.assertRaisesRegex(Hold, 'authority_handoff_receipt_custody'):
                self.remote.send(self.intent, b'fixture')
        self.assertEqual(self.worker.calls, [])
        self.assertEqual(self.retained.read_bytes(), overflow)

    def test_exact_retained_original_survives_restart_without_second_physical_effect(self):
        effect = asdict(OriginalEffect(self.op, 'terminal', TerminalEffect(self.op, 'verified', 'f'*64)))
        attempted, physical = [], {}

        def receiver(path, body, **kwargs):
            if path == '/v1/send':
                attempted.append(body)
                if physical:
                    self.assertEqual(body, physical[self.op.original_id])
                else:
                    physical[self.op.original_id] = body
                raise AuthorityUnavailable('fixture-lost-ack')
            if path == '/v1/provider/receipt':
                return ProviderReply(b'fixture-result', 'same-original', 'f'*64)
            if path == '/v1/original':
                return effect
            raise AssertionError(path)

        self.worker.request = receiver
        self.assertEqual(self.remote.send(self.intent, b'fixture').read(), b'fixture-result')
        old = (self.retained.stat().st_ino, self.retained.read_bytes())
        self.composition()
        self.assertEqual(self.remote.send(self.intent, b'fixture').read(), b'fixture-result')
        self.assertEqual(len(physical), 1)
        self.assertEqual(attempted[0], attempted[1])  # Existing same-original receiver contract.
        self.assertEqual((self.retained.stat().st_ino, self.retained.read_bytes()), old)
