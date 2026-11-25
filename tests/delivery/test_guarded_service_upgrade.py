"""Offline fixtures for the operator-facing, read-only preflight."""

import io
import json
from pathlib import Path
import runpy
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[2] / 'tools/delivery/guarded_service_upgrade.py'
PREFLIGHT = runpy.run_path(str(SOURCE))['preflight']
TOOL = PREFLIGHT.__globals__
UNIT = 'stokowski-foo.service'
STAGE = 'uif1313-idle-a22fbb2'
PIN = 'a' * 64


def encoded(value):
    return json.dumps(value, sort_keys=True).encode()


class UpgradePreflight(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.stage = Path(self.temp.name) / 'packets' / STAGE
        self.stage.mkdir(parents=True)
        self.targets = Path(self.temp.name) / 'targets'
        self.targets.mkdir()
        self.enterContext(patch.dict(TOOL, {'PACKETS': self.stage.parent, 'STAGES': self.targets,
                                         'protected_open': lambda path, limit: path.open('rb'),
                                         'protected_parents': lambda path: None}))
        self.enterContext(patch.object(TOOL['os'], 'getuid', return_value=1001))
        self.enterContext(patch.object(TOOL['os'], 'geteuid', return_value=1001))
        self.enterContext(patch.object(TOOL['pwd'], 'getpwuid',
                                      return_value=SimpleNamespace(pw_name='fixture-operator')))
        self.enterContext(patch.object(TOOL['time'], 'time', return_value=1000))
        self.enterContext(patch.dict(TOOL, {'stopped_unit_sha': lambda unit: PIN}))
        self.config = dict(schema='uif.stokowski-remote-idle/1', unit=UNIT, stage=STAGE,
                           origin=TOOL['HOST'], access_credential=TOOL['ACCESS_FILE'],
                           native_identity=dict(authority_id='a', session_id='n',
                                                deployment_sha256=PIN, policy_sha256=PIN),
                           coordinator_epoch=1, hold_reason='free_route_unavailable')
        self.review = dict(schema='uif.guarded-upgrade-review/1', unit=UNIT, stage=STAGE,
                           head=TOOL['PROFILES']['uif-idle'].reviewed_head, author='a', reviewer='b',
                           verdict='accept', parked_unit_sha256=PIN)
        self.quiescence = dict(schema='uif.guarded-upgrade-quiescence/1', unit=UNIT,
                               parked_unit_sha256=PIN, observed_at=990, inflight=0,
                               children_gone=True, unresolved_operation_ids=[])
        self.admission = dict(schema='uif.guarded-upgrade-admission/1', profile='uif-idle',
                              unit=UNIT, stage=STAGE, owner='fixture-operator',
                              owner_uid=1001, issued_at=980, expires_at=1100)

    def qualify(self, extra=None):
        parts = {'launch.py': b'# synthetic launcher fixture\n',
                 TOOL['IDLE_CONFIG']: encoded(self.config)}
        if extra:
            parts.update(extra)
        with tarfile.open(self.stage / 'bundle.tar', 'w') as archive:
            for name, data in parts.items():
                meta = tarfile.TarInfo(name)
                meta.size = len(data)
                archive.addfile(meta, io.BytesIO(data))
        pin = TOOL['digest']
        self.review.update(bundle_sha256=pin((self.stage / 'bundle.tar').read_bytes()),
                           launcher_sha256=pin(parts['launch.py']),
                           config_sha256=pin(parts[TOOL['IDLE_CONFIG']]))
        for name, payload in {'review': encoded(self.review), 'quiescence': encoded(self.quiescence),
                              'confirmation': encoded({'fixture_only': True})}.items():
            (self.stage / (name + '.json')).write_bytes(payload)
            self.admission[name + '_sha256'] = pin(payload)
        (self.stage / 'admission.json').write_bytes(encoded(self.admission))

    def test_exact_packet_can_only_yield_a_repeatable_read_only_plan(self):
        self.qualify()
        original = {p.name: p.read_bytes() for p in self.stage.iterdir()}
        plan = PREFLIGHT('uif-idle', STAGE)
        self.assertEqual(plan, PREFLIGHT('uif-idle', STAGE))
        self.assertFalse(plan['apply_supported'])
        self.assertEqual(plan['unit'], UNIT)
        self.assertEqual({p.name: p.read_bytes() for p in self.stage.iterdir()}, original)
        self.assertEqual(list(self.targets.iterdir()), [])

    def test_owner_and_independent_review_must_match(self):
        self.admission['owner_uid'] = 1002
        self.qualify()
        with self.assertRaisesRegex(ValueError, 'operator'):
            PREFLIGHT('uif-idle', STAGE)
        self.admission['owner_uid'] = 1001
        self.review['reviewer'] = 'A'
        self.qualify()
        with self.assertRaisesRegex(ValueError, 'independent acceptance'):
            PREFLIGHT('uif-idle', STAGE)

    def test_live_or_unsettled_unit_and_existing_stage_are_refused(self):
        self.quiescence['unresolved_operation_ids'] = ['retained']
        self.qualify()
        with self.assertRaisesRegex(ValueError, 'quiescence'):
            PREFLIGHT('uif-idle', STAGE)
        self.quiescence['unresolved_operation_ids'] = []
        self.qualify()
        with patch.dict(TOOL, {'stopped_unit_sha': runpy.run_path(str(SOURCE))['stopped_unit_sha']}):
            status = TOOL['STOPPED'] | {'ActiveState': 'active'}
            with patch.object(TOOL['subprocess'], 'check_output', side_effect=[
                    b'unit bytes', '\n'.join(f'{k}={v}' for k, v in status.items())]):
                with self.assertRaisesRegex(ValueError, 'unit is not stopped'):
                    PREFLIGHT('uif-idle', STAGE)
        (self.targets / STAGE).mkdir()
        with self.assertRaisesRegex(ValueError, 'target stage already exists'):
            PREFLIGHT('uif-idle', STAGE)

    def test_marker_normal_mode_and_tampering_are_refused(self):
        self.qualify({'ACTIVATION_AUTHORIZED': b'no'})
        with self.assertRaisesRegex(ValueError, 'unexpected bundle member'):
            PREFLIGHT('uif-idle', STAGE)
        self.config['route'] = {'test': True}
        self.qualify()
        with self.assertRaisesRegex(ValueError, 'unexpected record fields'):
            PREFLIGHT('uif-idle', STAGE)
        self.config.pop('route')
        self.qualify()
        (self.stage / 'bundle.tar').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'bundle pin'):
            PREFLIGHT('uif-idle', STAGE)

    def test_expired_admission_and_legacy_profile_do_not_read_host(self):
        self.admission['expires_at'] = 1000
        self.qualify()
        with patch.dict(TOOL, {'stopped_unit_sha': lambda unit: self.fail('unexpected host read')}):
            with self.assertRaisesRegex(ValueError, 'admission window'):
                PREFLIGHT('uif-idle', STAGE)
            with self.assertRaisesRegex(ValueError, 'existing owner-only'):
                PREFLIGHT('offmenu-legacy', STAGE)
