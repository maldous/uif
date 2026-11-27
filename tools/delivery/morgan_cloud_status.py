"""One read-only Morgan status probe through configured Codex HTTPS proxy."""

import json
import os
import sys
from urllib import request
from urllib.error import URLError
from urllib.parse import urlsplit

AUTHORITY = "uif-shared-authority.uif-black-brook-8df3.workers.dev"
URL = "https://" + AUTHORITY + "/v1/status"
SECRET_KEYS = ("UIF_MORGAN_ACCESS_CLIENT_ID", "UIF_MORGAN_ACCESS_CLIENT_SECRET")
MAX_RESPONSE = 16384


class StatusUnavailable(RuntimeError):
    """Safe, redacted status error."""


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate response field")
        result[key] = value
    return result


def _observe_authenticated():
    """Existing authenticated readback; trusted in-process callers only."""
    placeholders = tuple(os.environ.get(key) for key in SECRET_KEYS)
    if any(type(value) is not str or not 0 < len(value) <= 1024 or
           any(not 33 <= ord(char) <= 126 for char in value) for value in placeholders):
        raise StatusUnavailable("Morgan Cloud Access network secret bindings unavailable")
    proxy = request.getproxies().get("https")
    try:
        settings = urlsplit(proxy or "")
        valid = (settings.scheme in {"http", "https"} and settings.hostname
                 and (settings.port is None or 0 < settings.port <= 65535)
                 and not settings.query and not settings.fragment
                 and settings.path in ("", "/"))
    except ValueError:
        valid = False
    if not valid or request.proxy_bypass(AUTHORITY):
        raise StatusUnavailable("configured HTTPS proxy required for Morgan Cloud status")

    opener = request.build_opener(request.ProxyHandler({"https": proxy}), _NoRedirect())
    req = request.Request(URL, method="GET", headers={
        "CF-Access-Client-Id": placeholders[0],
        "CF-Access-Client-Secret": placeholders[1],
        "Accept": "application/json",
    })
    try:
        with opener.open(req, timeout=4) as response:
            if (response.status != 200 or response.geturl() != URL or
                    response.headers.get_content_type() != "application/json"):
                raise StatusUnavailable("Morgan Cloud authenticated status unavailable")
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise StatusUnavailable("Morgan Cloud status response exceeded its bound")
        result = json.loads(raw, object_pairs_hook=_unique_pairs)
    except (URLError, OSError, ValueError):
        raise StatusUnavailable("Morgan Cloud authenticated status unavailable") from None
    if (type(result) is not dict or result.get("role") != "morgan" or
            type(result.get("stopped")) is not bool or
            type(result.get("coordinator_epoch")) is not int or result["coordinator_epoch"] <= 0):
        raise StatusUnavailable("Morgan Cloud status identity or response shape not verified")
    return result


def observe():
    result = _observe_authenticated()
    return {key: result[key] for key in ("role", "stopped", "coordinator_epoch")}


def begin_identity_readback(expected):
    """Optional in-process renewal readback; standalone CLI stays redacted."""
    from uif.service_identity_readback import begin_same_role_readback
    return begin_same_role_readback(expected, role="morgan", observe=_observe_authenticated)


def main():
    if len(sys.argv) != 1:
        return 2
    try:
        print(json.dumps(observe(), sort_keys=True))
    except StatusUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
