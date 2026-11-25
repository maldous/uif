import hashlib
import json
from pathlib import Path
import runpy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[2] / "tools/delivery/private_windmill_idle_receiver.py"
RECEIVER = runpy.run_path(str(PATH))["main"]
GLOBALS = RECEIVER.__globals__
JOB = "12345678-1234-1234-1234-123456789abc"


class PrivateIdleReceiverTest(unittest.TestCase):
    def test_exact_protected_modules_and_configuration_only(self):
        stub = b"""
def record(raw, fields):
    import json
    result = json.loads(raw)
    if result.keys() != set(fields):
        raise ValueError("unexpected fixed pins")
    return result

def FixedWebhookIdleStart(**pins):
    return pins

def bind_private_webhook_idle(pins):
    return lambda audit_id: (audit_id, pins["operation_id"])
"""
        config = json.dumps(dict(operation_id="a"*64, source_head="b"*40,
            review_sha256="c"*64, bundle_sha256="d"*64, selection_sha256="e"*64)).encode()
        read = lambda name, _: stub if name.endswith(".py") else config
        runtime = SimpleNamespace(flags=SimpleNamespace(isolated=True, no_site=True),
                                  dont_write_bytecode=True, modules={})
        with (patch.dict(GLOBALS, {"read": read, "sys": runtime,
                    "CONTROLLER_SHA256": hashlib.sha256(stub).hexdigest()}),
              patch.object(GLOBALS["os"], "getresuid", return_value=(0, 0, 0))):
            self.assertEqual((JOB, "a"*64), RECEIVER([JOB]))
            with self.assertRaisesRegex(PermissionError, "isolated"):
                RECEIVER([JOB, "caller operation"])
            with patch.dict(GLOBALS, {"CONTROLLER_SHA256": "0"*64}):
                with self.assertRaisesRegex(PermissionError, "controller changed"):
                    RECEIVER([JOB])
