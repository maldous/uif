import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
import json
import os
import pwd
from pathlib import Path
import socket
import subprocess
from tempfile import TemporaryDirectory
import sys
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from uif.effect_authority import AuthorityIdentity
from uif.handoff_store import Hold
from uif.provider_pool import PoolRequest
from uif.quota_handoff import LinearKey
from uif.stokowski_cloudflare import launcher
from uif.stokowski_cloudflare import native_composition
from uif.stokowski_cloudflare.account_admission import AccountUsageEvidence, FreeRoute
from uif.stokowski_cloudflare.budget import BudgetRefused
from uif.stokowski_cloudflare.client import GLM_MODEL
from uif.stokowski_cloudflare.shared_admission import AccountUsageReceipt
SOURCE = Path(__file__).resolve().parents[2] / "tools/delivery"
with patch.object(sys, "path", [str(SOURCE), *sys.path]):
    import commissioned_normal_service as normal
    import compose_remote_native_launch as comp

STAGE = "/opt/uif-stokowski/uif1313-normal-0123"
HEAD, TREE = "a"*40, "b"*40
PINS = ["c"*64]*6
FIXTURE = ('''from pathlib import Path
import sys
_GuardedPath = Path
_GuardedStartUnit = "stokowski-foo.service"
_GuardedMarker = Path("/opt/uif-stokowski/uif1313-guarded-1f45bd56/ACTIVATION_AUTHORIZED")
_GuardedFlags = sys.flags
_GuardedArgv = sys.argv
_GuardedAdminFlag = "--admin"
source = "970929f77d4e24bc20fcba2864a4435b3a7791843eedc4874f4f3e66c4fb1aed"

def _guarded_startup_authorized(binding, verify):
    observed_binding, state = verify(object())
    if (False
           or observed_binding != binding or type(state) is not str or state != 'mounted'):
        return False
    if not _GuardedMarker.is_file():
        raise SystemExit("Stokowski activation is held; installed binding is parked.")
    return True

def guarded_staged_entry(*, startup_binding=None, startup_verifier=None):
    runner = orchestrator = publication = object()
    if not _GuardedMarker.is_file():
        raise SystemExit("Stokowski activation is held; installed binding is parked.")
    if not _guarded_startup_authorized(startup_binding, startup_verifier):
        raise SystemExit("Stokowski normal startup is held; owner-scoped shared authority is unavailable.")
    configure(runner, orchestrator, publication,
              policy=Path("test"))
if __name__ == "__main__":
    guarded_staged_entry()
''').encode()

class CommissionedServiceTest(unittest.TestCase):
    def build(self, mode=None, stage=STAGE, pins=PINS):
        raw = FIXTURE
        if mode is not None:
            raw = raw.replace(b'    runner = orchestrator', b'''    ROOT=Path("/opt/uif-stokowski/native-core-20261113-a1")
    sys.path.insert(0,str(ROOT/"python"))
    sys.path.append(str(ROOT/"web-deps"))
    runner = orchestrator''')
        with patch.object(comp, "PREIMAGE", sha256(raw).hexdigest()):
            return comp.compose(raw, stage, *pins, commissioned_source=mode)
    def test_legacy_unchanged_byte_for_byte(self):
        self.assertEqual(sha256(self.build()).hexdigest(), "15ca2dca92fbe8000b7bb620a0b7885d85c79ec02897730459d455334eb37e79")
    @unittest.skipUnless(os.getenv("UIF_EXACT_PARKED_FIXTURE"),
                         "set UIF_EXACT_PARKED_FIXTURE to the exact retained reviewed parked preimage")
    def test_exact_parked_compiler_preserves_legacy_and_replaces_every_old_marker_gate(self):
        raw=Path(os.environ["UIF_EXACT_PARKED_FIXTURE"]).read_bytes()
        self.assertEqual(sha256(raw).hexdigest(),comp.PREIMAGE)
        legacy=comp.compose(raw,STAGE,*PINS)
        self.assertEqual(sha256(legacy).hexdigest(),
           "b2052f3841c237ae098b84841cbb93ab96f35ca8c06b81210a77214bb690a7ad")
        generated=comp.compose(raw,STAGE,*PINS,commissioned_source=(HEAD,TREE))
        self.assertNotIn(b"_GuardedMarker.is_file",generated)
        self.assertEqual(generated.count(b"_VerifyCommissionedSelection()"),4)
        self.assertIn(b"return observed <= monotonic_ns() < valid_until",generated)
        code=compile(generated,"exact-reviewed-commissioned-launcher","exec")
        events=[]
        ns={"__name__":"exact_detached",
            "_VerifyCommissionedSelection":lambda:events.append("durable-selection")}
        exec(code,ns)
        ns["_GuardedFlags"]=SimpleNamespace(isolated=True,no_site=True,dont_write_bytecode=True)
        binding=(normal.UNIT,Path(STAGE).name,"authority","native",3,"b"*64,"c"*64)
        with patch("time.monotonic_ns",side_effect=[10,11,12]):
            self.assertTrue(ns["_guarded_startup_authorized"](
                binding,lambda req:(req,binding,"mounted",30),"mounted"))
        self.assertEqual(events,["durable-selection"])
        events.clear()
        with patch("time.monotonic_ns",side_effect=[10,11,30]):
            self.assertFalse(ns["_guarded_startup_authorized"](
                binding,lambda req:(req,binding,"mounted",30),"mounted"))
        self.assertEqual(events,["durable-selection"])
    def test_normal_imports_only_from_stage_and_requires_native_entry_files(self):
        adapter = b'# fixture adapter'
        generated = self.build((HEAD, TREE), pins=[sha256(adapter).hexdigest()]*6).decode()
        self.assertNotIn('native-core-20261113-a1', generated)
        self.assertNotIn('ROOT/"python"', generated)
        self.assertNotIn('ROOT/"web-deps"', generated)
        # Run the exact emitted import setup up to the real authority import.
        # Root ownership is covered by _CRaw's separate custody test.
        first = generated.index('    _Site = _GuardedPath(')
        last = generated.index('    from uif.stokowski_cloudflare.native_composition', first)
        with TemporaryDirectory() as fixture:
            root = Path(fixture)
            stage = root/'stage'
            site = stage/'venv/lib/python3.12/site-packages'
            foreign = root/'foreign'
            for origin, base in [('stage', site), ('foreign', foreign)]:
                pkg = base/'stokowski'
                pkg.mkdir(parents=True)
                for name in ('__init__', '__main__', 'config', 'runner', 'orchestrator', 'publication'):
                    (pkg/(name+'.py')).write_text(f'ORIGIN = {origin!r}')
                (base/'example_dependency.py').write_text(f'ORIGIN = {origin!r}')
            adapters = site/'uif/stokowski_cloudflare'
            adapters.mkdir(parents=True)
            for name in ('native_composition', 'remote_authority', 'remote_source', 'native', 'turn', 'launcher'):
                (adapters/(name+'.py')).write_bytes(adapter)
            setup = generated[first:last].replace(repr(STAGE), repr(str(stage)))
            program = (f'import sys as _StartupSys, hashlib as _StartupHash\n'
                       f'from pathlib import Path as _GuardedPath\n'
                       f'_StartupSys.path.insert(0, {str(foreign)!r})\n'
                       f'def _CRaw(path, limit): return path.read_bytes()\n'
                       f'if True:\n{setup}'
                       'import stokowski.config, example_dependency\n'
                       'print(stokowski.config.ORIGIN, example_dependency.ORIGIN)')

            def run():
                return subprocess.run([sys.executable, '-I', '-S', '-B', '-c', program],
                                      capture_output=True)

            selected = run()
            self.assertEqual(selected.returncode, 0, selected.stderr)
            self.assertEqual(selected.stdout.strip(), b'stage stage')
            for missing in ['__init__', 'config', '__main__']:
                member = site/'stokowski'/(missing+'.py')
                contents = member.read_bytes()
                member.unlink()
                denied = run()
                self.assertEqual(denied.returncode, 78, denied.stderr)
                self.assertIn(b'commissioned native source is unavailable', denied.stderr)
                self.assertEqual(denied.stdout, b'')
                member.write_bytes(contents)

    def test_legacy_and_wrong_scope_cannot_be_commissioned(self):
        for path in normal.LEGACY:
            with self.assertRaises(ValueError): self.build((HEAD,TREE),path)
        with self.assertRaises(ValueError): self.build((HEAD,"not-a-tree"))
    def test_render_selection_exact_record_matches_existing_entrypoint(self):
        artifacts = dict(release_sha256="1"*64, launcher_sha256="2"*64,
                         interpreter_sha256="3"*64, authority_config_sha256="4"*64)
        rendered = normal.render_normal_selection(STAGE, source_head=HEAD, source_tree=TREE, **artifacts)
        self.assertEqual(json.loads(rendered), dict(schema="uif.stokowski-commissioned-normal/1",
            mode="normal", unit=normal.UNIT, stage=STAGE, source_head=HEAD, source_tree=TREE, **artifacts))
        self.assertEqual(rendered, normal.render_normal_selection(
            STAGE, source_head=HEAD, source_tree=TREE, **dict(reversed(list(artifacts.items())))))
        for invalid in normal.LEGACY:
            with self.assertRaises(ValueError):
                normal.render_normal_selection(invalid, source_head=HEAD, source_tree=TREE, **artifacts)
        with self.assertRaises(ValueError):
            normal.render_normal_selection(STAGE, source_head=HEAD, source_tree=TREE,
                                          **(artifacts | {"release_sha256": "observed"}))

    def test_dropin_boot_sandbox_and_restart(self):
        out=normal.render_normal_dropin(STAGE).decode()
        for text in ("After=network-online.target","StartLimitIntervalSec=300s","StartLimitBurst=3",
                     "User=user","Group=user","ExecStart="+STAGE+"/venv/bin/python3.12 -I -S -B ",
                     "CapabilityBoundingSet=\nAmbientCapabilities=\n","NoNewPrivileges=yes",
                     "Restart=on-failure","RestartSec=110s","RestartPreventExitStatus=78","KillSignal=SIGTERM"):
            self.assertIn(text,out)
        self.assertNotIn("ExecStart=!",out)
    def test_restart_spacing_preserves_recovery_and_manual_burst_limit(self):
        # Model systemd's anchored start-limit window. A refused automatic
        # start does not leave another automatic attempt queued.
        unit = normal.render_normal_dropin(STAGE).decode()
        fields = dict(line.split("=", 1) for line in unit.splitlines() if "=" in line)
        window = int(fields["StartLimitIntervalSec"].removesuffix("s"))
        burst = int(fields["StartLimitBurst"])
        restart = int(fields["RestartSec"].removesuffix("s"))
        self.assertGreater(burst * restart, window)

        def limiter():
            begin, count = None, 0
            def allowed(now):
                nonlocal begin, count
                if begin is None or now - begin > window:
                    begin, count = now, 0
                count += 1
                return count <= burst
            return allowed

        def recover(delay, outage):
            allowed = limiter()
            for now in range(0, 2 * outage, delay):
                if not allowed(now):
                    return None
                if now >= outage:
                    return now
            return None

        # The previous 20-second setting latched before recovery. The revised
        # unit still attempts automatically beyond a five-minute outage.
        self.assertIsNone(recover(20, 350))
        self.assertEqual(recover(restart, 350), 440)
        manual = limiter()
        self.assertEqual([manual(now) for now in (0, 1, 2, 3, 301)],
                         [True, True, True, False, True])

    def test_compiled_subprocess_without_installed_commission_fails_permanently(self):
        raw=self.build((HEAD,TREE))
        compile(raw,"compiled","exec")
        self.assertNotIn(b"_GuardedMarker.is_file",raw)
        run=subprocess.run([sys.executable,"-I","-S","-B","-c",
           "exec(compile("+repr(raw)+",'commissioning-fixture','exec'),"+
            "{'__name__':'__main__','__file__':'/invalid/launch.py'})"],capture_output=True)
        self.assertEqual(run.returncode,78)
        self.assertIn(b"commissioned normal selection is not verified",run.stderr)
    def test_actual_compiled_entry_waits_for_existing_verifier_then_configures(self):
        raw=self.build((HEAD,TREE))
        found=[]
        hold=ModuleType("uif.handoff_store"); remote=ModuleType("uif.stokowski_cloudflare.remote_authority")
        class Hold(ValueError): pass
        class AuthorityUnavailable(Hold): pass
        hold.Hold=Hold; remote.AuthorityUnavailable=AuthorityUnavailable
        def sleep(seconds):
            self.assertEqual(seconds,5)
            self.assertNotIn("configure",found)
            found.append("sleep")
        ns={"__name__":"private_test",
            "_NativeSinks":lambda *a:found.append("sinks"),
            "configure":lambda *a,**kw:found.append("configure"),
            "_RemoteComposition":type("FakeComposition",(),{"options":lambda s:{}})(),
            "_VerifyCommissionedSelection":lambda:None}
        replies=iter([("other","mounted"),("own","stopped_idle"),("own","mounted")])
        with patch.dict(sys.modules,{x.__name__:x for x in (hold,remote)}), \
                patch.object(launcher.time,"sleep",side_effect=sleep), \
                patch.object(launcher,"_idle_notify") as notify, patch("builtins.print"):
            def verify(callback):
                def recorded(request):
                    found.append("verify")
                    return callback(request)
                return recorded
            exec(compile(raw,"compiled","exec"),ns)
            with self.assertRaises(SystemExit) as held:
                ns["guarded_staged_entry"](startup_binding="own",startup_verifier=verify(lambda _:next(replies)),
                                            idle_reason="free_route_unavailable")
            self.assertEqual(held.exception.code,78)
            self.assertEqual(found,[])
            ns["guarded_staged_entry"](startup_binding="own",startup_verifier=verify(lambda _:next(replies)))
            self.assertEqual(found,["verify","sleep","verify","sleep","verify","sinks","configure"])
            found.clear()
            retry=iter([AuthorityUnavailable("transport"),Hold("authority_http_409"),("own","mounted")])
            def challenge(_request):
                value=next(retry)
                if isinstance(value,Exception): raise value
                return value
            ns["guarded_staged_entry"](startup_binding="own",startup_verifier=verify(challenge))
            self.assertEqual(found,["verify","sleep","verify","sleep","verify","sinks","configure"])
            found.clear()
            def wrong_install(_request): raise Hold("authority_installation_changed")
            with self.assertRaises(SystemExit) as held:
                ns["guarded_staged_entry"](startup_binding="own",startup_verifier=verify(wrong_install))
            self.assertEqual(held.exception.code,78)
            self.assertEqual(found,["verify"])
            self.assertIn(f"RestartPreventExitStatus={held.exception.code}",
                          normal.render_normal_dropin(STAGE).decode())
            self.assertTrue(notify.call_args_list)
            self.assertTrue(all("READY=" not in call.args[0] for call in notify.call_args_list))

    def test_commissioned_no_route_uses_fresh_mounted_challenge_before_idle(self):
        unit, stage = normal.UNIT, Path(STAGE).name
        identity = AuthorityIdentity('authority', 'a'*64, 'b'*64, 'installed-service')
        data = dict(schema='uif.stokowski-remote-authority/1', unit=unit, stage=stage,
                    route=None, origin='https://authority.example.test',
                    access_credential='/etc/no-route-fixture.json', native_identity=asdict(identity),
                    coordinator_epoch=1)
        path = '/protected/remote-authority.json'
        challenges = []

        def reply(endpoint, body, *, timeout):
            self.assertEqual((endpoint, timeout), ('/v1/startup/read', 4))
            self.assertEqual(body, {'unit': unit, 'stage': stage, 'nonce': body['nonce']})
            self.assertRegex(body['nonce'], '^[a-f0-9]{48}$')
            challenges.append(body['nonce'])
            return dict(body, **asdict(identity), coordinator_epoch=1, expires_at=int(time.time()) + 4)

        with (patch.object(native_composition, 'protected_record', return_value=data) as local,
              patch.object(native_composition, 'AccessTransport') as make_transport,
              patch.object(native_composition, 'RemoteAuthority') as remote,
              patch.object(launcher, 'serve_commissioned_no_route_idle') as idle):
            transport = make_transport.return_value
            transport.request.side_effect = reply
            value, args = native_composition.load_guarded_startup(path, unit, stage)
            self.assertIsNone(value)
            self.assertIs(args['commissioned_no_route'], True)
            self.assertNotIn('idle_reason', args)
            verifier = args['startup_verifier']
            binding = args['startup_binding']
            request = ('uif.stokowski.native-start/1', binding, time.monotonic_ns()+5_000_000_000)
            receipt = verifier(request)
            self.assertIs(receipt[0], request)
            self.assertEqual(receipt[1:3], (binding, 'mounted'))
            self.assertLessEqual(receipt[3], request[2])
            self.assertGreater(receipt[3], time.monotonic_ns())

            drift = data | {'coordinator_epoch': 2}
            local.return_value = drift
            with self.assertRaisesRegex(Hold, 'authority_installation_changed'):
                verifier(request)
            self.assertEqual(transport.request.call_count, 1)
            local.side_effect = [data, drift]
            with self.assertRaisesRegex(Hold, 'authority_installation_changed'):
                verifier(request)
            local.side_effect = None
            local.return_value = data

            for invalid in ({'nonce': 'different'}, {'coordinator_epoch': 2}, {'expires_at': 0}):
                transport.request.side_effect = lambda *a, **kw: reply(*a, **kw) | invalid
                with self.assertRaisesRegex(Hold, 'authority_start_receipt'):
                    verifier(request)
            transport.request.side_effect = reply

            configured = Mock()
            install_sinks = Mock()
            selected = Mock()
            ns = {'__name__': 'test_commissioned_no_route', '_RemoteComposition': value,
                  '_NativeSinks': install_sinks, 'configure': configured,
                  '_VerifyCommissionedSelection': selected}
            exec(compile(self.build((HEAD,TREE)), 'compiled', 'exec'), ns)

            def guard(wanted, verifier, state):
                self.assertEqual(state, 'mounted')
                attempt = (request[0], wanted, time.monotonic_ns()+5_000_000_000)
                observed = verifier(attempt)
                self.assertIs(observed[0], attempt)
                return observed[1:3] == (wanted, state)

            ns['_guarded_startup_authorized'] = guard
            idle.side_effect = lambda verify: self.assertIs(verify(), True)
            ns['guarded_staged_entry'](**args)
            selected.assert_called_once_with()
            idle.assert_called_once()
            configured.assert_not_called()
            install_sinks.assert_not_called()
            remote.assert_not_called()
            self.assertEqual(len(challenges), 7)
            self.assertEqual(len(set(challenges)), len(challenges))

    def test_commissioned_no_route_remains_alive_and_reports_authority_recovery(self):
        with TemporaryDirectory() as scratch, socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify:
            address = str(Path(scratch) / 'notify')
            notify.bind(address)
            notify.settimeout(5)
            env = os.environ | {'NOTIFY_SOCKET': address,
                                'PYTHONPATH': str(SOURCE.parents[1] / 'src')}
            code = '''
from uif.stokowski_cloudflare import launcher
launcher._COMMISSIONED_IDLE_POLL_SECONDS = .02
answers = iter([False, True])
launcher.serve_commissioned_no_route_idle(lambda: next(answers, True))
'''
            with subprocess.Popen([sys.executable, '-B', '-c', code], env=env, text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
                try:
                    ready = notify.recv(512).decode()
                    expected = ('STATUS=Stokowski commissioned idle: free_route_unavailable; '
                                'verified mounted route required; admissions and effects closed')
                    self.assertEqual(ready, 'READY=1\n' + expected)
                    self.assertEqual(notify.recv(512).decode(),
                                     'STATUS=Stokowski blocked: authority_unavailable; admissions and effects closed')
                    self.assertEqual(notify.recv(512).decode(), expected)
                    self.assertIsNone(process.poll())
                    process.terminate()
                    self.assertEqual(notify.recv(512).decode(), 'STOPPING=1\nSTATUS=Stokowski idle shutdown')
                    _, error = process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 0, error)
                finally:
                    if process.poll() is None:
                        process.kill()


    def test_persisted_normal_record_checks_exact_members_and_mode(self):
        code=compile("if True:\n"+normal.normal_entry_pins(STAGE,HEAD,TREE),"selection","exec")
        stage=Path(STAGE); launcher=stage/"launch.py"; interpreter=stage/"venv/bin/python3.12"
        config=stage/"selector-v1/remote-authority.json"; selected=Path(normal.SELECTION)
        members={str(launcher):b"launcher",str(interpreter):b"interpreter",
                 str(config):b'{"schema":"uif.stokowski-remote-authority/1"}'}
        record=dict(schema="uif.stokowski-commissioned-normal/1",mode="normal",
          unit=normal.UNIT,stage=STAGE,source_head=HEAD,source_tree=TREE,release_sha256="d"*64,
          launcher_sha256=sha256(members[str(launcher)]).hexdigest(),
          interpreter_sha256=sha256(members[str(interpreter)]).hexdigest(),
          authority_config_sha256=sha256(members[str(config)]).hexdigest())
        def verify(candidate, tamper=False, writable=False):
            data=dict(members, **{str(selected):json.dumps(candidate).encode()})
            if tamper: data[str(launcher)]=b"changed launcher"
            nodes={}
            def open_at(part, flags, *, dir_fd=None):
                path=Path(part) if dir_fd is None else Path(nodes[dir_fd])/part
                fd=len(nodes)+10; nodes[fd]=str(path); return fd
            def fstat(fd):
                node=nodes[fd]
                mode=0o100444 if node in data else 0o40755
                if writable and node==str(launcher): mode|=0o020
                return SimpleNamespace(st_uid=0, st_mode=mode, st_nlink=1,
                   st_size=len(data.get(node,b"")),st_mtime_ns=1,st_ctime_ns=2)
            def opened(fd,*a):
                class Raw(io.BytesIO):
                    def fileno(self): return fd
                return Raw(data[nodes[fd]])
            with patch.object(pwd,"getpwnam",return_value=SimpleNamespace(pw_uid=1000,pw_gid=1000)), \
                patch.object(os,"getresuid",return_value=(1000,)*3), \
                patch.object(os,"getresgid",return_value=(1000,)*3), \
                patch.object(os,"open",side_effect=open_at),patch.object(os,"close"), \
                patch.object(os,"fstat",side_effect=fstat), \
                patch.object(os,"fdopen",side_effect=opened), \
                patch.object(sys,"argv",[str(launcher)]),patch.object(sys,"executable",str(interpreter)), \
                patch.object(sys,"dont_write_bytecode",True), \
                patch.object(sys,"flags",SimpleNamespace(isolated=True,no_site=True)):
                exec(code,{"_GuardedPath":Path,"__file__":str(launcher)})
        verify(record)
        for candidate,tamper,writable in (
                (dict(record,mode="idle"),False,False),(record,True,False),(record,False,True)):
            with self.subTest(mode=candidate["mode"],tamper=tamper,writable=writable), \
                    self.assertRaises(SystemExit) as held:
                verify(candidate,tamper,writable)
            self.assertEqual(held.exception.code,78)


class CommissionedSchedulerTest(unittest.IsolatedAsyncioTestCase):
    async def test_configured_scheduler_selects_newly_verified_route_without_restart(self):
        key = LinearKey(*(f'00000000-0000-0000-0000-00000000000{i}' for i in (1, 2, 3)))
        now = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)
        accounts = dict(zip(('UIF', 'OffMenu', 'ANQR', 'CIX', 'CQ'), (n*32 for n in 'abcde')))
        exact = FreeRoute(accounts['OffMenu'], GLM_MODEL, None, 'f'*64)
        evidence = AccountUsageEvidence(exact, '2026-11-25', now, now+timedelta(seconds=120),
                                        0, 0, True, True)
        receipt = AccountUsageReceipt(object(), 'd'*64, evidence)
        port = SimpleNamespace(read_account_usage=Mock(), launcher_options=lambda: {})
        authenticated = False
        remote = SimpleNamespace(routes={'OffMenu': exact}, clock=lambda: now,
                                 workspace_id=key.workspace_id, project_id=key.project_id,
                                 read_usage=Mock(return_value=receipt), quota_issuer=receipt.issuer,
                                 verify_usage=Mock(side_effect=lambda *_: authenticated),
                                 ports=port, account_ports={'OffMenu': port})
        options = native_composition.NativeComposition(remote, '/fixture').options()
        discovery = options['provider_discovery']
        discovery.interval, discovery.timeout = .01, .01
        route = discovery.adapters[0].route
        request = PoolRequest(key, 'maldous/offmenu', 'a'*64, 'b'*64, 'fresh-operation',
                              'ordinary-software', 'author', 'new', (route.route_id,))
        starts = []

        async def normal_start(owner):
            nonlocal authenticated
            starts.append(owner)
            self.assertTrue(discovery.active)

            def select(**state):
                return owner.uif_select_provider(request, **(
                    dict(owner_paused=False, pending_original=False) | state))

            self.assertIsNone(select().route)
            with self.assertRaises(BudgetRefused):
                options['read_account_usage'](10)
            while dict(discovery.status())[route.route_id] != 'reader-unavailable':
                await asyncio.sleep(.001)
            self.assertIsNone(select().route)

            authenticated = True
            while select().route is None:
                await asyncio.sleep(.001)
            self.assertEqual(select().route, route)
            self.assertIsNone(select(owner_paused=True).route)
            self.assertIsNone(select(pending_original=True).route)
            self.assertEqual(options['read_account_usage'](10).usage_snapshots, {'OffMenu': evidence})
            return 'finished'

        class IssueOwner:
            _dispatch, _run_worker, _release_slot, _is_eligible = Mock(), AsyncMock(), Mock(), Mock()

        scheduler = type('Multi', (), {'start': normal_start})
        run_turn = Mock()
        runner = SimpleNamespace(run_turn=run_turn)
        native = SimpleNamespace(run_turn=runner.run_turn, Orchestrator=IssueOwner,
                                 MultiOrchestrator=scheduler, logger=Mock())
        policy = dict(accounts=accounts, projects=['OffMenu'], credentials='/etc/stokowski-fixture',
                      model=GLM_MODEL, workers_ai_strict_free_routes={'OffMenu': exact.credential_epoch_sha256})
        with (TemporaryDirectory() as scratch,
              patch.object(launcher, '_protected_json', return_value=policy) as read_policy):
            launcher.configure(runner, native, SimpleNamespace(), policy=Path('/etc/owner-fixture.json'),
                               database=Path(scratch)/'budget.sqlite', **options)
            owner = scheduler()
            self.assertEqual(await asyncio.wait_for(owner.start(), 2), 'finished')
        self.assertEqual(starts, [owner])
        self.assertGreaterEqual(remote.read_usage.call_count, 2)
        read_policy.assert_called_once_with(Path('/etc/owner-fixture.json'))
        port.read_account_usage.assert_not_called()
        run_turn.assert_not_called()
        self.assertFalse(discovery.active)
