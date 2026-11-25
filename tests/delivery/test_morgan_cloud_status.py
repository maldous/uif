import json
from email.message import Message
from pathlib import Path
import runpy
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

TOOL = runpy.run_path(str(Path(__file__).resolve().parents[2] /
                           "tools/delivery/morgan_cloud_status.py"))["observe"]
API = TOOL.__globals__
FIXTURE = {key: "synthetic-placeholder-" + str(index) for index, key in enumerate(API["SECRET_KEYS"])}


class MorganCloudStatusTest(unittest.TestCase):
    def setUp(self):
        self.env = self.enterContext(patch.dict(API["os"].environ, FIXTURE, clear=True))
        http = API["request"]
        self.enterContext(patch.object(http, "getproxies", return_value={"https": "http://proxy.invalid:8080"}))
        self.bypass = self.enterContext(patch.object(http, "proxy_bypass", return_value=False))
        self.builder = self.enterContext(patch.object(http, "build_opener"))
        self.response = self.builder.return_value.open.return_value.__enter__.return_value
        self.response.status = 200
        self.response.geturl.return_value = API["URL"]
        self.response.headers = Message()
        self.response.headers["Content-Type"] = "application/json"

    def test_projects_only_authenticated_morgan_binding_status(self):
        self.response.read.return_value = json.dumps(dict(role="morgan", stopped=True,
            coordinator_epoch=1, private_extra="not printed")).encode()
        self.assertEqual(dict(role="morgan", stopped=True, coordinator_epoch=1), TOOL())
        req = self.builder.return_value.open.call_args.args[0]
        self.assertEqual("GET", req.method)
        self.assertEqual(API["URL"], req.full_url)
        self.assertIsNone(req.data)
        proxy, deny_redirect = self.builder.call_args.args
        self.assertEqual({"https": "http://proxy.invalid:8080"}, proxy.proxies)
        self.assertIsNone(deny_redirect.redirect_request())

    def test_unbound_credentials_or_bypassed_proxy_do_not_open_connection(self):
        del self.env[API["SECRET_KEYS"][0]]
        with self.assertRaisesRegex(RuntimeError, "bindings unavailable"):
            TOOL()
        self.env.update(FIXTURE)
        self.bypass.return_value = True
        with self.assertRaisesRegex(RuntimeError, "proxy required"):
            TOOL()
        self.builder.assert_not_called()

    def test_errors_are_redacted_and_native_role_or_ambiguous_state_fails(self):
        for raw in (b'{"role":"native","stopped":true,"coordinator_epoch":1}',
                    b'{"role":"morgan","stopped":true,"stopped":false,"coordinator_epoch":1}',
                    b'{"role":"morgan","stopped":true,"coordinator_epoch":true}'):
            self.response.read.return_value = raw
            with self.assertRaises(RuntimeError):
                TOOL()
        self.builder.return_value.open.side_effect = HTTPError(API["URL"], 302,
            "opaque fixture body should not appear", {}, None)
        with self.assertRaisesRegex(RuntimeError, "^Morgan Cloud authenticated status unavailable$"):
            TOOL()
