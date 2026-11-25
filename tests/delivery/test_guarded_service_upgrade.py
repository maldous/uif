"""Offline fixtures for the operator-facing, read-only preflight."""

import io
import json
from contextlib import contextmanager
from dataclasses import replace
import fcntl
import os
from pathlib import Path
import runpy
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch


SOURCE = Path(__file__).resolve().parents[2] / 'tools/delivery/guarded_service_upgrade.py'
PREFLIGHT = runpy.run_path(str(SOURCE))['preflight']
TOOL = PREFLIGHT.__globals__
APPLY = TOOL['apply']
REAL_STOPPED = TOOL['stopped_unit_sha']
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
                if name == TOOL['INTERPRETER']:
                    meta.mode = 0o755
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

    def test_linux_adapter_atomically_selects_and_holds_the_same_start_lock(self):
        self.exercise_linux_workflow(interrupted=False)

    def test_interrupted_start_cannot_reuse_the_consumed_intent(self):
        self.exercise_linux_workflow(interrupted=True)

    def exercise_linux_workflow(self, *, interrupted):
        base = self.stage.parent
        stage = self.targets / STAGE
        receipts = base / 'receipts'
        receipts.mkdir()
        lock = base / 'selector.lock'
        lock.write_text('root qualified lock\n')
        lock.chmod(0o644)
        selector = base / 'selection.json'
        old = TOOL['encode']({'mode': 'parked', 'stage': TOOL['PARKED_STAGE']})
        selector.write_bytes(old)
        dropin = base / 'dropin.conf'
        self.enterContext(patch.dict(TOOL, {'ROOT': base, 'LOCK': lock, 'SELECTOR': selector,
                                          'RECEIPTS': receipts, 'DROPIN': dropin,
                                          'stopped_unit_sha': REAL_STOPPED}))
        bootstrapped = b'detached effective unit bytes'
        self.review['parked_unit_sha256'] = TOOL['digest'](bootstrapped)
        self.quiescence['parked_unit_sha256'] = self.review['parked_unit_sha256']
        self.qualify({TOOL['INTERPRETER']: b'fixture interpreter'})
        with tarfile.open(self.stage / 'bundle.tar', 'r:') as bundle:
            for member in bundle:
                path = stage / member.name
                path.parent.mkdir(parents=True, exist_ok=True)
                with bundle.extractfile(member) as file:
                    path.write_bytes(file.read())
                path.chmod(member.mode)
        dropin.write_bytes(TOOL['selector_dropin']())
        effective = dropin.read_text().split('ExecStart=\nExecStart=!', 1)[1].strip()
        effective_restart = 'on-failure'
        manager_cgroup = '/system.slice/' + UNIT
        process_cgroup = manager_cgroup
        parent = 1
        edge_uids = (0, 0, 0)

        def systemctl(argv, **_):
            if argv[0] == TOOL['JOURNALCTL']:
                self.assertIn('_PID=42', argv)
                self.assertIn('_SYSTEMD_INVOCATION_ID=' + 'a' * 32, argv)
                return 'Stokowski idle: free_route_unavailable; admissions and effects closed\n'
            if 'cat' in argv:
                return bootstrapped
            if '--property=ExecStart' in argv:
                return f'ExecStart={{ argv[]={effective} ; }}'
            if '--property=Restart' in argv:
                return f'Restart={effective_restart}\n'
            if '--property=ActiveState,SubState,MainPID,InvocationID' in argv:
                return 'ActiveState=active\nSubState=running\nMainPID=42\nInvocationID=' + 'a' * 32
            if any(arg.startswith('--property=Id,') for arg in argv):
                return (f'Id={UNIT}\nUser=user\nGroup=user\nSupplementaryGroups=\nType=simple\n'
                        f'Restart={effective_restart}\nNoNewPrivileges=yes\nMainPID=42\n'
                        f'InvocationID={"a" * 32}\nControlGroup={manager_cgroup}\nActiveState=active\n')
            return '\n'.join(f'{k}={v}' for k, v in TOOL['STOPPED'].items())

        self.enterContext(patch.object(TOOL['subprocess'], 'check_output', side_effect=systemctl))
        original_lstat = Path.lstat

        def lstat(path):
            observed = original_lstat(path)
            if path in (stage, stage / TOOL['MARKER']):
                return SimpleNamespace(st_uid=0, st_mode=observed.st_mode, st_nlink=observed.st_nlink,
                                       st_size=observed.st_size)
            return observed

        self.enterContext(patch.object(Path, 'lstat', lstat))
        real_read_text = Path.read_text

        def read_text(path, *args, **kwargs):
            if path == Path('/proc/self/cgroup'):
                return '0::' + process_cgroup + '\n'
            return real_read_text(path, *args, **kwargs)

        self.enterContext(patch.object(Path, 'read_text', read_text))
        self.enterContext(patch.object(os, 'getppid', side_effect=lambda: parent))
        self.enterContext(patch.object(os, 'getresuid', side_effect=lambda: edge_uids))
        self.enterContext(patch.object(os, 'getpid', return_value=42))
        plan = PREFLIGHT('uif-idle', STAGE, sealed_stage=True)
        target = TOOL['sealed_selection'](plan)
        admission = TOOL['ApplyAdmission'](
            'uif-idle', UNIT, STAGE, 'e'*64, TOOL['plan_digest'](plan),
            TOOL['plan_digest'](TOOL['lock_identity'](lock.lstat())),
            TOOL['digest'](old), TOOL['digest'](target), 990, 1080,
        )
        issued_packet = self.stage / 'linux-apply.json'
        key = base / 'synthetic-workflow-key'
        keygen = TOOL['SSH_KEYGEN']
        subprocess.run([keygen, '-q', '-t', 'ed25519', '-N', '', '-f', str(key)],
                       check=True, capture_output=True)
        signers = base / 'fixture-signers'
        signers.write_bytes(b'fixture-operator ' + key.with_suffix('.pub').read_bytes())
        self.enterContext(patch.dict(TOOL, {'ACTIVATION_SIGNERS': signers}))

        def sign_file(raw, name, namespace):
            path = self.stage / (name + '.json')
            path.with_suffix('.json.sig').unlink(missing_ok=True)
            path.write_bytes(raw)
            subprocess.run([keygen, '-Y', 'sign', '-f', str(key), '-n', namespace, str(path)],
                           check=True, capture_output=True)

        def sign(plan, name, schema, action, namespace):
            sign_file(encoded({'schema': schema, 'action': action, 'operation_id': plan['operation_id'],
                               'preflight_sha256': TOOL['plan_digest'](plan),
                               'issued_at': 990, 'expires_at': 1080}), name, namespace)

        # Replace the generic preflight fixture with output from the actual installer issuer.
        confirmation_path = self.stage / 'confirmation.json'
        admission_path = self.stage / 'admission.json'
        confirmation_path.unlink()
        admission_path.unlink()
        installer = runpy.run_path(str(SOURCE.with_name('issue_foo_idle_installer.py')))['ceremony']
        loader = base / SOURCE.name
        loader.write_bytes(SOURCE.read_bytes())
        fixture_pwd = SimpleNamespace(getpwnam=lambda name: SimpleNamespace(pw_name=name, pw_uid=1001))
        self.enterContext(patch.dict(installer.__globals__, {
            'SIGNERS': signers, 'LOADER': loader, 'pwd': fixture_pwd,
        }))
        loader_pin = TOOL['bytes_pin'](loader)
        installed = SimpleNamespace(**TOOL)
        crossed = TOOL['encode'](installer(installed, loader_pin, 'f' * 64, issue=False))
        sign_file(crossed, 'installer-request', installer.__globals__['NAMESPACE'])
        alternate = installer(installed, loader_pin, 'f' * 64, issue=True)
        self.assertEqual(alternate['confirmation_sha256'], TOOL['digest'](crossed))
        # Separate synthetic installer run in the same disposable fixture.
        confirmation_path.unlink()
        admission_path.unlink()
        original = TOOL['encode'](installer(installed, loader_pin, admission.operation_id, issue=False))
        sign_file(original, 'installer-request', installer.__globals__['NAMESPACE'])
        installer_receipt = installer(installed, loader_pin, admission.operation_id, issue=True)
        self.assertEqual(installer_receipt['confirmation_sha256'], TOOL['digest'](original))
        self.assertEqual(installer_receipt['admission_sha256'], TOOL['bytes_pin'](admission_path))
        self.assertEqual(selector.read_bytes(), old)

        @contextmanager
        def crossed_installer():
            paths = (confirmation_path, admission_path, issued_packet)
            previous = [path.read_bytes() for path in paths]
            operator = json.loads(previous[1])
            operator['confirmation_sha256'] = TOOL['digest'](crossed)
            packet = json.loads(previous[2])
            operator_bytes = TOOL['encode'](operator)
            packet['operator_admission_sha256'] = TOOL['digest'](operator_bytes)
            confirmation_path.write_bytes(crossed)
            admission_path.write_bytes(operator_bytes)
            issued_packet.write_bytes(TOOL['encode'](packet))
            try:
                yield
            finally:
                for path, saved in zip(paths, previous):
                    path.write_bytes(saved)

        with patch.object(TOOL['os'], 'getuid', return_value=0), patch.object(
                TOOL['os'], 'geteuid', return_value=0):
            prepare_apply = TOOL['prepare_apply']
            preview = prepare_apply(admission.operation_id)
            self.assertFalse(issued_packet.exists())
            with self.assertRaises(FileNotFoundError):
                prepare_apply(admission.operation_id, issue=True)
            def sign_apply(plan):
                sign(plan, 'apply-confirmation', 'uif.foo-idle-apply-confirmation/1',
                     'issue_linux_apply', TOOL['APPLY_NAMESPACE'])

            sign_apply(preview['preflight'])
            issued = prepare_apply(admission.operation_id, issue=True)
            self.assertEqual(issued['preflight'], preview['preflight'])
            self.assertEqual(issued, prepare_apply(admission.operation_id, issue=True))
            self.assertEqual(selector.read_bytes(), old)
            with self.assertRaisesRegex(ValueError, 'original installer operation'):
                prepare_apply('f' * 64)
            admission = TOOL['LinuxIdleHost']().load_admission()
            self.assertEqual(admission.expires_at, plan['valid_until'])
            with crossed_installer(), self.assertRaisesRegex(ValueError, 'original installer operation'):
                APPLY('uif-idle', STAGE, admission=admission, host=TOOL['LinuxIdleHost']())
            self.assertEqual(list(receipts.iterdir()), [])
            with lock.open('rb') as running:
                fcntl.flock(running, fcntl.LOCK_SH | fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError):
                    APPLY('uif-idle', STAGE, admission=admission, host=TOOL['LinuxIdleHost']())
            result = APPLY('uif-idle', STAGE, admission=admission, host=TOOL['LinuxIdleHost']())
            self.assertEqual(APPLY('uif-idle', STAGE, admission=admission,
                                   host=TOOL['LinuxIdleHost']()), result)
            check_activation = TOOL['activation_preflight']
            before = {path: path.read_bytes() for path in receipts.iterdir()}
            plan = check_activation(admission.operation_id)
            self.assertEqual(plan['mode'], 'read_only')
            self.assertEqual(plan['operation_id'], admission.operation_id)
            self.assertEqual(plan['original_owner_uid'], self.admission['owner_uid'])
            self.assertEqual(plan['bundle_sha256'], self.review['bundle_sha256'])
            self.assertEqual(plan['selected_receipt_sha256'], TOOL['plan_digest'](result))
            self.assertEqual(plan['selection_sha256'], TOOL['digest'](target))
            self.assertEqual(plan['unit_sha256'], self.review['parked_unit_sha256'])
            self.assertEqual(before, {path: path.read_bytes() for path in receipts.iterdir()})
            with self.assertRaisesRegex(ValueError, 'operation binding'):
                check_activation('f' * 64)
            with lock.open('rb') as running:
                fcntl.flock(running, fcntl.LOCK_SH | fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError):
                    check_activation(admission.operation_id)
            completion = receipts / (admission.operation_id + '.selected.json')
            completion.write_bytes(encoded({}))
            with self.assertRaisesRegex(ValueError, 'receipts'):
                check_activation(admission.operation_id)
            completion.write_bytes(before[completion])
            selector.write_bytes(old)
            with self.assertRaisesRegex(ValueError, 'readback changed'):
                check_activation(admission.operation_id)
            selector.write_bytes(target)
            marker = stage / TOOL['MARKER']
            marker.touch()
            with self.assertRaisesRegex(ValueError, 'both markers absent'):
                check_activation(admission.operation_id)
            marker.unlink()
        self.assertEqual(selector.read_bytes(), target)
        self.assertEqual(len(list(receipts.iterdir())), 3)
        self.assertFalse((stage / TOOL['MARKER']).exists())
        marker = stage / TOOL['MARKER']
        effective_restart = 'no'
        with patch.object(TOOL['pwd'], 'getpwnam') as lookup_service_account:
            lookup_service_account.return_value = SimpleNamespace(pw_uid=1001)
            with self.assertRaises(FileNotFoundError):
                TOOL['serve_idle_stage']()
            with patch.object(os, 'execv') as refused_exec:
                marker.touch()
                with self.assertRaisesRegex(ValueError, 'signed start intent is missing'):
                    TOOL['serve_idle_stage']()
                marker.write_bytes(b'{}')
                with self.assertRaisesRegex(ValueError, 'unexpected record fields'):
                    TOOL['serve_idle_stage']()
                refused_exec.assert_not_called()
        marker.unlink()
        effective_restart = 'on-failure'
        (self.stage / 'start-quiescence.json').write_bytes(
            (self.stage / 'quiescence.json').read_bytes(),
        )

        real_run = subprocess.run
        dispatches = []
        consumed_path = receipts / (admission.operation_id + '.start-consumed.json')

        def exec_with_lock(*_):
            self.assertTrue(consumed_path.is_file())
            with lock.open('rb') as competing:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(competing, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if interrupted:
                raise OSError('simulated interruption after consumption')

        def dispatch(argv, **kwargs):
            nonlocal parent, process_cgroup, edge_uids
            if argv[0] == keygen:
                return real_run(argv, **kwargs)
            self.assertEqual(argv, [TOOL['SYSTEMCTL'], '--no-pager', '--job-mode=fail', 'start', UNIT])
            dispatches.append(argv)
            with lock.open('rb') as unlocked:
                fcntl.flock(unlocked, fcntl.LOCK_SH | fcntl.LOCK_NB)
            self.assertTrue((receipts / (admission.operation_id + '.start-intent.json')).is_file())
            self.assertTrue((stage / TOOL['MARKER']).is_file())
            edge_uids = (0, 0, 1001)
            with self.assertRaisesRegex(ValueError, 'must be launched by the system manager'):
                TOOL['serve_idle_stage']()
            edge_uids = (0, 0, 0)
            parent = 10
            with self.assertRaisesRegex(ValueError, 'must be launched by the system manager'):
                TOOL['serve_idle_stage']()
            parent = 1
            process_cgroup = '/system.slice/not-stokowski.service'
            with self.assertRaisesRegex(ValueError, 'did not attest'):
                TOOL['serve_idle_stage']()
            process_cgroup = manager_cgroup
            with lock.open('rb') as contender:
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError):
                    TOOL['serve_idle_stage']()
            self.assertFalse(consumed_path.exists())
            with crossed_installer(), self.assertRaisesRegex(ValueError, 'original signed intent'):
                TOOL['serve_idle_stage']()
            self.assertFalse(consumed_path.exists())
            TOOL['serve_idle_stage']()

        with patch.object(TOOL['os'], 'getuid', return_value=0), patch.object(
                TOOL['os'], 'geteuid', return_value=0):
            check = TOOL['activation_preflight']
            start_plan = check(admission.operation_id, prepare_start=True)
            self.assertIn('start_quiescence_sha256', start_plan)
            with self.assertRaises(FileNotFoundError):
                check(admission.operation_id, start=True)
            sign(start_plan, 'activation-confirmation', 'uif.foo-idle-activation-confirmation/1',
                 'authorize_idle_marker', TOOL['ACTIVATION_NAMESPACE'])
            with self.assertRaisesRegex(ValueError, 'current operation'):
                check(admission.operation_id, start=True)
            sign(start_plan, 'activation-confirmation', 'uif.foo-idle-activation-confirmation/1',
                 'authorize_idle_marker_and_start', TOOL['ACTIVATION_NAMESPACE'])
            with crossed_installer(), self.assertRaisesRegex(ValueError, 'original installer operation'):
                check(admission.operation_id, start=True)
            with self.assertRaisesRegex(ValueError, 'one-start policy'):
                check(admission.operation_id, start=True)
            self.assertFalse(marker.exists())
            self.assertFalse((receipts / (admission.operation_id + '.start-intent.json')).exists())
            effective_restart = 'no'
            with patch.object(subprocess, 'run', side_effect=dispatch), patch.dict(
                    TOOL, {'drop_idle_privileges': lambda: None}):
                with patch.object(os, 'execv', side_effect=exec_with_lock) as execute:
                    if interrupted:
                        with self.assertRaisesRegex(OSError, 'interruption after consumption'):
                            check(admission.operation_id, start=True)
                        self.assertFalse((receipts / (admission.operation_id + '.started.json')).exists())
                    else:
                        receipt = check(admission.operation_id, start=True)
                        self.assertEqual(receipt['main_pid'], 42)
                        self.assertEqual(receipt['invocation_id'], 'a' * 32)
                    executable = str(stage / TOOL['INTERPRETER'])
                    execute.assert_called_once_with(
                        executable, [executable, '-I', '-S', '-B', str(stage / 'launch.py')],
                    )
                    with self.assertRaisesRegex(ValueError, 'already consumed'):
                        TOOL['serve_idle_stage']()
                    execute.assert_called_once()
            self.assertEqual(len(dispatches), 1)
            original_marker = marker.read_bytes()
            marker_data = json.loads(original_marker)
            marker_data['selection_sha256'] = 'f' * 64
            marker.write_bytes(TOOL['encode'](marker_data))
            with patch.object(os, 'execv') as refused_exec:
                with self.assertRaisesRegex(ValueError, 'no longer binds the selected stage'):
                    TOOL['serve_idle_stage']()
                refused_exec.assert_not_called()
            marker.write_bytes(original_marker)
            with patch.object(TOOL['time'], 'time', return_value=1050):
                with self.assertRaisesRegex(ValueError, 'marker expired'):
                    TOOL['serve_idle_stage']()

    def test_reviewed_dropin_matches_fixed_start_contract(self):
        dropin = SOURCE.with_name('60-uif1313-idle-selector.conf')
        self.assertEqual(dropin.read_bytes(), runpy.run_path(str(SOURCE))['selector_dropin']())
        self.assertIn(b'\nRestart=no\n', dropin.read_bytes())

    def test_privileged_edge_must_drop_to_the_service_ids_without_capabilities(self):
        account = SimpleNamespace(pw_uid=1001, pw_gid=1001, pw_name='user',
                                  pw_dir='/home/user', pw_shell='/bin/sh')
        self.enterContext(patch.object(TOOL['pwd'], 'getpwnam', return_value=account))
        self.enterContext(patch.object(TOOL['grp'], 'getgrnam', return_value=SimpleNamespace(gr_gid=1001)))
        self.enterContext(patch.object(os, 'getgrouplist', return_value=[1001, 1002]))
        self.enterContext(patch.object(os, 'getgroups', return_value=[1001, 1002]))
        self.enterContext(patch.object(os, 'getresuid', return_value=(1001, 1001, 1001)))
        self.enterContext(patch.object(os, 'getresgid', return_value=(1001, 1001, 1001)))
        self.enterContext(patch.dict(os.environ))
        credentials = Mock()
        for name in ('initgroups', 'setresgid', 'setresuid'):
            self.enterContext(patch.object(os, name, getattr(credentials, name)))
        status = 'NoNewPrivs: 1\nCapPrm: 0\nCapEff: 0\nCapAmb: 0\n'
        with patch.object(Path, 'read_text', return_value=status):
            TOOL['drop_idle_privileges']()
        self.assertEqual(credentials.mock_calls, [
            call.initgroups('user', 1001), call.setresgid(1001, 1001, 1001),
            call.setresuid(1001, 1001, 1001),
        ])
        with patch.object(Path, 'read_text', return_value=status.replace('CapEff: 0', 'CapEff: 1')):
            with self.assertRaisesRegex(ValueError, 'privileges did not drop'):
                TOOL['drop_idle_privileges']()

    def test_activation_confirmation_requires_the_exact_enrolled_signer_and_operation(self):
        plan = {'operation_id': 'e' * 64, 'selection_sha256': PIN}
        raw = encoded({
            'schema': 'uif.foo-idle-activation-confirmation/1', 'action': 'authorize_idle_marker',
            'operation_id': plan['operation_id'], 'preflight_sha256': TOOL['plan_digest'](plan),
            'issued_at': 990, 'expires_at': 1040,
        })
        confirmation = self.stage / 'activation-confirmation.json'
        confirmation.write_bytes(raw)
        signers = self.stage / 'allowed_signers'
        verify = TOOL['verify_activation_confirmation']
        self.enterContext(patch.dict(TOOL, {'ACTIVATION_SIGNERS': signers}))
        with self.assertRaisesRegex(ValueError, 'has not enrolled'):
            verify(self.stage, plan, 'fixture-operator')
        key = self.stage / 'synthetic-test-key'
        keygen = TOOL['SSH_KEYGEN']
        subprocess.run([keygen, '-q', '-t', 'ed25519', '-N', '', '-f', str(key)],
                       check=True, capture_output=True)
        public_key = key.with_suffix('.pub').read_bytes()
        signers.write_bytes(b'fixture-operator ' + public_key)
        subprocess.run([keygen, '-Y', 'sign', '-f', str(key), '-n',
                        TOOL['ACTIVATION_NAMESPACE'], str(confirmation)],
                       check=True, capture_output=True)
        self.assertEqual(verify(self.stage, plan, 'fixture-operator')['confirmation_sha256'],
                         TOOL['digest'](raw))
        with self.assertRaisesRegex(ValueError, 'current operation'):
            verify(self.stage, {**plan, 'selection_sha256': 'd' * 64}, 'fixture-operator')
        with self.assertRaisesRegex(ValueError, 'original Foo operator'):
            verify(self.stage, plan, 'different-fixture-operator')
        confirmation.write_bytes(raw + b'\n')
        with self.assertRaisesRegex(ValueError, 'signature'):
            verify(self.stage, plan, 'fixture-operator')
        confirmation.write_bytes(raw)
        with patch.object(TOOL['time'], 'time', return_value=1040):
            with self.assertRaisesRegex(ValueError, 'window'):
                verify(self.stage, plan, 'fixture-operator')


class DetachedHost:
    """One test adapter for the protected host contract, never an installer."""

    def __init__(self):
        self.plan = {'schema': 'uif.guarded-upgrade-preflight/1', 'mode': 'read_only',
                     'profile': 'uif-idle', 'unit': UNIT, 'stage': STAGE,
                     'head': TOOL['PROFILES']['uif-idle'].reviewed_head, 'valid_until': 1040}
        self.admission = TOOL['ApplyAdmission'](
            'uif-idle', UNIT, STAGE, 'e'*64, TOOL['plan_digest'](self.plan),
            'b'*64, PIN, 'c'*64, 990, 1080,
        )
        self.selected = PIN
        self.intent = self.completion = self.fence = None
        self.locked = False
        self.events = []
        self.after_intent = lambda: None

    @contextmanager
    def lock(self, unit, custody):
        assert (unit, custody) == (UNIT, 'b'*64) and not self.locked
        self.locked = True
        try:
            yield self
        finally:
            self.locked = False

    def verify_admission(self, admission):
        assert self.locked
        return admission == self.admission

    def verify_custody(self):
        return self.locked

    def receipts(self, operation, prior):
        assert self.locked and prior == PIN
        return (self.intent if operation == self.fence else None,
                self.completion if operation == self.fence else None, self.fence)

    def preflight(self, profile, stage):
        assert self.locked and (profile, stage) == ('uif-idle', STAGE)
        self.events.append('preflight')
        return self.plan

    def selection_sha256(self):
        assert self.locked
        return self.selected

    def verify_idle_stage(self, plan, target):
        return self.locked and plan == self.plan and target == 'c'*64

    def create_intent(self, operation, prior, intent):
        assert self.locked and prior == PIN and self.intent is None and self.fence is None
        self.events.append('intent')
        self.fence, self.intent = operation, intent
        self.after_intent()

    def select_idle(self, stage, prior, target, deadline):
        assert self.locked and (stage, prior, target, deadline) == (STAGE, self.selected, 'c'*64, 1040)
        self.events.append('select')
        self.selected = target

    def create_completion(self, operation, result):
        assert self.locked and operation == self.fence and self.completion is None
        self.events.append('completion')
        self.completion = result


class UpgradeApply(unittest.TestCase):
    def setUp(self):
        self.host = DetachedHost()
        self.clock = self.enterContext(patch.object(TOOL['time'], 'time', return_value=1000))

    def execute(self):
        return APPLY('uif-idle', STAGE, admission=self.host.admission, host=self.host)

    def test_selected_once_under_lock_and_retry_reads_the_original_receipt(self):
        result = self.execute()
        self.assertEqual(self.host.events, ['preflight', 'intent', 'preflight', 'select', 'completion'])
        self.clock.return_value = 1200
        self.assertEqual(self.execute(), result)
        self.assertEqual(self.host.events.count('select'), 1)
        self.host.selected = PIN
        with self.assertRaisesRegex(ValueError, 'reconciliation'):
            self.execute()

    def test_admission_expiry_or_quiescence_drift_after_intent_holds(self):
        self.host.after_intent = lambda: setattr(self.clock, 'return_value', 1040)
        with self.assertRaisesRegex(ValueError, 'quiescence expired under lock'):
            self.execute()
        self.assertEqual(self.host.events, ['preflight', 'intent', 'preflight'])
        self.clock.return_value = 1000
        with self.assertRaisesRegex(ValueError, 'reconciliation'):
            self.execute()
        self.host = DetachedHost()
        self.host.after_intent = lambda: self.host.plan.update(valid_until=1050)
        with self.assertRaisesRegex(ValueError, 'preflight differs'):
            self.execute()
        self.assertNotIn('select', self.host.events)

    def test_unknown_effect_cannot_replay_under_same_or_new_id(self):
        def interrupted(*args):
            self.host.selected = 'c'*64
            raise OSError('fixture lost acknowledgement')

        self.host.select_idle = interrupted
        with self.assertRaises(OSError):
            self.execute()
        with self.assertRaisesRegex(ValueError, 'reconciliation'):
            self.execute()
        self.host.admission = replace(self.host.admission, operation_id='f'*64)
        with self.assertRaisesRegex(ValueError, 'reconciliation'):
            self.execute()
        self.assertIsNone(self.host.completion)

    def test_missing_admission_or_lock_and_legacy_profile_never_mutate(self):
        with patch.object(self.host, 'verify_admission', return_value=False):
            with self.assertRaisesRegex(ValueError, 'trusted host admission'):
                self.execute()
        with patch.object(self.host, 'verify_custody', return_value=False):
            with self.assertRaisesRegex(ValueError, 'selector lock'):
                self.execute()
        with patch.object(self.host, 'lock') as lock:
            with self.assertRaisesRegex(ValueError, 'existing owner-only'):
                APPLY('offmenu-legacy', STAGE, admission=self.host.admission, host=self.host)
            lock.assert_not_called()
        self.assertEqual(self.host.events, [])
