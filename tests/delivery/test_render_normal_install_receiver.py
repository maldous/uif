from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


DELIVERY = Path(__file__).parents[2] / "tools/delivery"
sys.path.insert(0, str(DELIVERY))
import commit_commissioned_normal_selection as transaction
from render_normal_install_receiver import SOURCE_MODULES, render_normal_install_receiver


DRIVER = """
from contextlib import contextmanager
import json
import os
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

receiver, case = sys.argv[1:]
root = Path(receiver).parent
main = runpy.run_path(receiver)["main"]
scope = main.__globals__
assert sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode
assert scope["ROOT"] == Path("/var/lib/uif-stokowski/idle-upgrade/normal-install-v1")


class FixtureOwner:
    def __getattr__(self, name):
        return getattr(os, name)

    def getresuid(self):
        return (0, 0, 0)

    def fixture_info(self, info):
        assert info.st_uid == os.getuid()
        fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size",
                  "st_mtime_ns", "st_ctime_ns")
        return SimpleNamespace(st_uid=0, **{name: getattr(info, name) for name in fields})

    def fstat(self, fd):
        return self.fixture_info(os.fstat(fd))

    def stat(self, *args, **kwargs):
        return self.fixture_info(os.stat(*args, **kwargs))


opened = []
loaded = []
handed_off = []


@contextmanager
def fixture_directory():
    opened.append(True)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield fd
    finally:
        os.close(fd)


def expect_handoff(resolve):
    original, archive, selector, dropin = resolve()
    assert original.operation_id == "a" * 64
    assert archive == b"detached test archive"
    assert selector == b"detached test selector"
    assert dropin == b"detached test dropin"
    handed_off.append(True)
    return {"status": "fixture_handoff"}


load_exact_sources = scope["_load_modules"]


def load_fixture_sources(inputs):
    modules = load_exact_sources(inputs)
    assert tuple(modules) == tuple(name for name, _ in scope["SOURCES"])
    for name, module in modules.items():
        assert Path(module.__file__).parent == scope["ROOT"] and name in sys.modules
    loaded.append(True)
    installer = modules["install_commissioned_normal_selection"]
    installer._stage_and_select_from_trusted_owner = expect_handoff
    return modules


scope["_protected_directory"] = fixture_directory
scope["_load_modules"] = load_fixture_sources
scope["os"] = FixtureOwner()
sys.argv = [receiver] + (["injected argument"] if case == "args" else [])
if case == "valid":
    assert main() == {"status": "fixture_handoff"} and handed_off
else:
    try:
        main()
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "opened": bool(opened),
                          "loaded": bool(loaded), "handed_off": bool(handed_off)}))
    else:
        raise AssertionError("receiver accepted fixture failure")
"""


def pin(raw):
    return sha256(raw).hexdigest()


class RenderNormalInstallReceiver(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.sources = {name: (DELIVERY / (name + ".py")).read_bytes()
                        for name in SOURCE_MODULES}
        self.artifacts = {
            "bundle.tar": b"detached test archive",
            "selection.json": b"detached test selector",
            "90-uif-commissioned-normal.conf": b"detached test dropin",
        }
        stage = transaction.NormalRuntimeStageApproval(
            "b" * 64, "uif1313-normal-fixture", pin(self.artifacts["bundle.tar"]),
            "c" * 64, "d" * 64, "e" * 64,
        )
        original = transaction.SelectionOriginal(
            "a" * 64, stage, None, None, stage.prior_dropin_sha256,
            pin(self.artifacts["selection.json"]),
            pin(self.artifacts["90-uif-commissioned-normal.conf"]),
            transaction.effective.NormalEffectiveOriginal(
                "/opt/uif-stokowski/uif1313-normal-fixture", "1" * 64, "2" * 64, 0, 0, (), ()
            ),
        )
        # This original exists only inside the disposable test. The tested
        # owner action is a sentinel and performs no host operations.
        self.raw = transaction.encode_selection_original(original)
        self.receiver = self.root / "private_windmill_normal_install_receiver.py"
        self.original_pin = pin(self.raw)
        self.receiver.write_bytes(render_normal_install_receiver(self.sources, self.original_pin))
        for name, data in self.sources.items():
            (self.root / (name + ".py")).write_bytes(data)
        for name, data in {**self.artifacts, "selection-original.json": self.raw}.items():
            (self.root / name).write_bytes(data)

    def run_fixture(self, case):
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-B", "-c", DRIVER, str(self.receiver), case],
            check=True, capture_output=True, text=True, cwd=self.root, timeout=15,
        )
        return json.loads(result.stdout) if result.stdout else None

    def test_render_is_deterministic_and_checks_exact_review_inputs(self):
        reversed_sources = dict(reversed(list(self.sources.items())))
        self.assertEqual(render_normal_install_receiver(reversed_sources, self.original_pin),
                         self.receiver.read_bytes())
        with self.assertRaises(ValueError):
            render_normal_install_receiver(self.sources, "not an independent digest")
        missing = {name: self.sources[name] for name in SOURCE_MODULES[1:]}
        with self.assertRaises(ValueError):
            render_normal_install_receiver(missing, self.original_pin)
        with self.assertRaises(SyntaxError):
            render_normal_install_receiver(
                {**self.sources, SOURCE_MODULES[0]: b"def /"}, self.original_pin)

    def test_isolated_loader_uses_exact_source_and_hands_off_one_original(self):
        self.assertIsNone(self.run_fixture("valid"))

    def test_caller_arguments_rejected_before_opening_protected_directory(self):
        result = self.run_fixture("args")
        self.assertIn("with no arguments", result["error"])
        self.assertFalse(result["opened"])
        self.assertFalse(result["loaded"])
        self.assertFalse(result["handed_off"])
        invoked = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(self.receiver), "unexpected"],
            capture_output=True, text=True, cwd=self.root, timeout=15,
        )
        self.assertNotEqual(invoked.returncode, 0)
        self.assertIn("with no arguments", invoked.stderr)

    def test_bad_source_and_original_refused_before_import(self):
        for name in (SOURCE_MODULES[-1] + ".py", "selection-original.json"):
            with self.subTest(name=name):
                path = self.root / name
                old = path.read_bytes()
                path.write_bytes(old + b" ")
                result = self.run_fixture("changed")
                self.assertIn("input pin differs", result["error"])
                self.assertFalse(result["loaded"])
                self.assertFalse(result["handed_off"])
                path.unlink()
                result = self.run_fixture("missing")
                self.assertIn("No such file or directory", result["error"])
                self.assertFalse(result["loaded"])
                self.assertFalse(result["handed_off"])
                path.write_bytes(old)

    def test_original_alone_cannot_supply_its_expected_digest(self):
        self.receiver.write_bytes(render_normal_install_receiver(self.sources, "f" * 64))
        result = self.run_fixture("wrong independent pin")
        self.assertIn("input pin differs", result["error"])
        self.assertFalse(result["loaded"])
        self.assertFalse(result["handed_off"])

    def test_all_artifact_pins_checked_before_handoff(self):
        for name, data in self.artifacts.items():
            with self.subTest(name=name):
                path = self.root / name
                path.write_bytes(data + b" ")
                result = self.run_fixture("changed")
                self.assertIn("input pin differs", result["error"])
                self.assertTrue(result["loaded"])
                self.assertFalse(result["handed_off"])
                path.unlink()
                result = self.run_fixture("missing")
                self.assertIn("No such file or directory", result["error"])
                self.assertTrue(result["loaded"])
                self.assertFalse(result["handed_off"])
                path.write_bytes(data)

    def test_linked_or_writable_input_is_refused_even_with_correct_bytes(self):
        path = self.root / "selection.json"
        spare = self.root / "outside"
        path.rename(spare)
        path.symlink_to(spare)
        self.assertFalse(self.run_fixture("symlink")["handed_off"])
        path.unlink()
        os.link(spare, path)
        result = self.run_fixture("hardlink")
        self.assertIn("unprotected normal installer input", result["error"])
        self.assertFalse(result["handed_off"])
        spare.unlink()
        path.chmod(0o664)
        result = self.run_fixture("writable")
        self.assertIn("unprotected normal installer input", result["error"])
        self.assertFalse(result["handed_off"])
