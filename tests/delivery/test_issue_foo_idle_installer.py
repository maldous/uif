"""Detached installer ceremony with a disposable OpenSSH signer."""

from contextlib import contextmanager
from pathlib import Path
import runpy
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


DELIVERY = Path(__file__).resolve().parents[2] / 'tools/delivery'
CEREMONY = runpy.run_path(str(DELIVERY / 'issue_foo_idle_installer.py'))['ceremony']
OWNER = CEREMONY.__globals__
LOADER = runpy.run_path(str(DELIVERY / 'guarded_service_upgrade.py'))
OPERATION = 'a' * 64


class FooInstallerCeremony(unittest.TestCase):
    def setUp(self):
        fixture = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(fixture)
        self.packet = self.root / LOADER['FIXED_STAGE']
        self.packet.mkdir()
        self.lock = self.root / 'selector.lock'
        self.lock.write_text('detached lock')
        self.signers = self.root / 'allowed_signers'
        self.loader = self.root / 'loader.py'
        self.loader.write_bytes(b'detached accepted loader')
        self.dropin = self.root / 'idle.conf'
        self.dropin.write_bytes(LOADER['selector_dropin']())
        self.selector = self.root / 'selection.json'
        self.selector.write_bytes(LOADER['encode']({'mode': 'parked', 'stage': LOADER['PARKED_STAGE']}))
        self.profile = LOADER['PROFILES'][LOADER['PROFILE_NAME']]
        self.unit_pin = 'b' * 64
        self.review = {
            'schema': 'uif.guarded-upgrade-review/1', 'verdict': 'accept',
            'author': 'fixture-author', 'reviewer': 'fixture-reviewer',
            'unit': self.profile.unit, 'stage': self.packet.name, 'head': self.profile.reviewed_head,
            'bundle_sha256': 'c' * 64, 'launcher_sha256': 'd' * 64, 'config_sha256': 'e' * 64,
            'parked_unit_sha256': self.unit_pin,
        }
        self.witness = {
            'schema': 'uif.guarded-upgrade-quiescence/1', 'unit': self.profile.unit,
            'parked_unit_sha256': self.unit_pin, 'observed_at': 980, 'inflight': 0,
            'children_gone': True, 'unresolved_operation_ids': [],
        }
        (self.packet / 'review.json').write_bytes(LOADER['encode'](self.review))
        (self.packet / 'quiescence.json').write_bytes(LOADER['encode'](self.witness))

        @contextmanager
        def fixture_lock(*, shared):
            self.assertFalse(shared)
            with self.lock.open('rb') as stream:
                yield stream

        self.core = SimpleNamespace(**{
            **LOADER, 'PACKETS': self.root, 'LOCK': self.lock, 'DROPIN': self.dropin,
            'SELECTOR': self.selector, 'protected_open': lambda path, _limit: path.open('rb'),
            'read_record': lambda root, name: (root / name).read_bytes(),
            'bytes_pin': lambda path: LOADER['digest'](path.read_bytes()),
            'sealed_selection': lambda review: (self.packet / 'fixture-bundle').read_bytes(),
            'selector_lock': fixture_lock, 'no_marker': lambda _stage: True,
            'stopped_unit_sha': lambda _unit: self.unit_pin,
        })
        (self.packet / 'fixture-bundle').write_bytes(b'fixed sealed selection')
        self.enterContext(patch.dict(OWNER, {'LOADER': self.loader, 'SIGNERS': self.signers}))
        self.enterContext(patch.dict(LOADER['publish'].__globals__, {'protected_parents': lambda _: None}))
        self.enterContext(patch.object(OWNER['time'], 'time', return_value=1000))
        self.enterContext(patch.object(OWNER['pwd'], 'getpwnam',
                                       return_value=SimpleNamespace(pw_name='fixture-owner', pw_uid=1001)))
        command = LOADER['selector_dropin']().decode().split('ExecStart=\nExecStart=', 1)[1].strip().removeprefix('!')
        self.enterContext(patch.object(subprocess, 'check_output', return_value=f'ExecStart={{ argv[]={command} ; }}'))
        self.key = self.root / 'disposable-test-key'
        self.keygen('-q', '-t', 'ed25519', '-N', '', '-f', str(self.key))
        self.signers.write_bytes(b'fixture-owner ' + self.key.with_suffix('.pub').read_bytes())

    def keygen(self, *args):
        subprocess.run([OWNER['SSH_KEYGEN'], *args], check=True, capture_output=True)

    def run_ceremony(self, *, issue, operation=OPERATION):
        return CEREMONY(self.core, self.core.bytes_pin(self.loader), operation, issue=issue)

    def sign_request(self, request, *, namespace=None):
        path = self.packet / OWNER['REQUEST']
        path.with_suffix('.json.sig').unlink(missing_ok=True)
        raw = self.core.encode(request)
        path.write_bytes(raw)
        self.keygen('-Y', 'sign', '-f', str(self.key), '-n', namespace or OWNER['NAMESPACE'], str(path))
        return raw

    def test_issues_exact_original_pair_and_recovers_only_matching_partial(self):
        request = self.run_ceremony(issue=False)
        raw = self.sign_request(request)
        confirmation = self.packet / 'confirmation.json'
        admission = self.packet / 'admission.json'
        confirmation.write_bytes(raw)
        result = self.run_ceremony(issue=True)
        self.assertEqual(result, self.run_ceremony(issue=True))
        self.assertEqual(confirmation.read_bytes(), raw)
        self.assertEqual(result['admission_sha256'], self.core.bytes_pin(admission))
        self.assertEqual(result['confirmation_sha256'], self.core.digest(raw))
        other = self.run_ceremony(issue=False, operation='f' * 64)
        self.sign_request(other)
        with self.assertRaisesRegex(ValueError, 'original installer pair'):
            self.run_ceremony(issue=True, operation='f' * 64)
        self.assertEqual(confirmation.read_bytes(), raw)

    def test_refuses_wrong_signature_namespace_and_expired_witness(self):
        request = self.run_ceremony(issue=False)
        self.sign_request(request, namespace='unrelated-fixture-purpose')
        with self.assertRaisesRegex(ValueError, 'signature refused'):
            self.run_ceremony(issue=True)
        self.sign_request(request)
        with patch.object(OWNER['time'], 'time', return_value=1040):
            with self.assertRaisesRegex(ValueError, 'fresh independently observed'):
                self.run_ceremony(issue=True)
        self.assertFalse((self.packet / 'admission.json').exists())
        self.assertFalse((self.packet / 'confirmation.json').exists())

    def test_refuses_missing_enrollment_and_drifted_bundle_or_unit(self):
        request = self.run_ceremony(issue=False)
        self.sign_request(request)
        signer = self.signers.read_bytes()
        self.signers.unlink()
        with self.assertRaises(FileNotFoundError):
            self.run_ceremony(issue=True)
        self.signers.write_bytes(signer)
        (self.packet / 'fixture-bundle').write_bytes(b'changed sealed selection')
        with self.assertRaisesRegex(ValueError, 'does not match'):
            self.run_ceremony(issue=True)
        self.unit_pin = 'f' * 64
        with self.assertRaisesRegex(ValueError, 'reviewed stopped idle bootstrap'):
            self.run_ceremony(issue=True)
        self.assertFalse((self.packet / 'admission.json').exists())


if __name__ == '__main__':
    unittest.main()
