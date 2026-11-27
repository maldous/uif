from hashlib import sha256
import io
import json
import os
import pwd
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
from uif.stokowski_cloudflare import launcher
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
    def build(self, mode=None, stage=STAGE):
        with patch.object(comp,"PREIMAGE",sha256(FIXTURE).hexdigest()):
            return comp.compose(FIXTURE,stage,*PINS,commissioned_source=mode)
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
    def test_legacy_and_wrong_scope_cannot_be_commissioned(self):
        for path in normal.LEGACY:
            with self.assertRaises(ValueError): self.build((HEAD,TREE),path)
        with self.assertRaises(ValueError): self.build((HEAD,"not-a-tree"))
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
