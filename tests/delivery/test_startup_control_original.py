import unittest
from dataclasses import replace
from unittest.mock import Mock
from uif.handoff_store import Hold, digest
from uif.stokowski_cloudflare.startup_control_original import (
    STARTUP_BIND, TRANSITION_ORIGINAL, StartupBindOriginal, observe_original,
)


class StartupControlOriginalTests(unittest.TestCase):
    def setUp(self):
        self.original = StartupBindOriginal('original-bind', 'stokowski-foo.service',
                                           'reviewed-stage', 'native-session', 4, 2000, 'f'*64)
        self.ack = dict(request_id='original-bind', unit='stokowski-foo.service',
                        stage='reviewed-stage')
        self.read = Mock(return_value=self.ack)
        self.args = dict(recorded_fingerprint=self.original.fingerprint(),
                         installation_sha256='a'*64, control_session_id='control-session',
                         read=self.read)

    def test_matches_canonical_wire_and_uses_actual_control_lookup(self):
        self.assertEqual(self.original.body(), dict(
            **self.ack, session_id='native-session', coordinator_epoch=4,
            valid_until=2000, evidence_sha256='f'*64))
        self.assertEqual(self.original.fingerprint(),
                         digest(dict(path=STARTUP_BIND, body=self.original.body())))
        self.assertEqual(observe_original(self.original, **self.args), dict(
            **self.ack, status='original_acknowledged', native_startup_verified=False))
        self.read.assert_called_once_with(TRANSITION_ORIGINAL, dict(
            installation_sha256='a'*64, session_id='control-session', request_id='original-bind'))

    def test_changed_original_or_native_actor_never_calls_port(self):
        with self.assertRaisesRegex(Hold, 'retained_startup_original_required'):
            observe_original(replace(self.original, coordinator_epoch=5), **self.args)
        with self.assertRaisesRegex(Hold, 'startup_control_must_not_be_native'):
            observe_original(self.original, **(self.args | {'control_session_id':'native-session'}))
        self.read.assert_not_called()

    def test_missing_or_changed_reply_never_retries_or_submits(self):
        for reply in (None, dict(self.ack, stage='other'), dict(self.ack, ready=True)):
            self.read.reset_mock()
            self.read.return_value = reply
            with self.assertRaisesRegex(Hold, 'startup_original_acknowledgement_unverified'):
                observe_original(self.original, **self.args)
            self.read.assert_called_once()
        self.read.reset_mock()
        self.read.side_effect = OSError("unknown")
        with self.assertRaises(OSError):
            observe_original(self.original, **self.args)
        self.read.assert_called_once()
