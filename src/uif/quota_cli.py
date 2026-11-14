"""Bounded JSON adapter for an existing authenticated Foo command transport.

No network, shell execution, installation or service control occurs here. The
transport still uses its existing command/cwd/job_id/timeout_seconds fields.
This CLI's authenticated principal is the actual OS uid. Installed transport
adapters may use Registry with their independently authenticated principal.
Claim/effect admission additionally needs an owner-installed RecoveryVerifier;
this conservative standalone adapter cannot manufacture one from input JSON.
"""

import argparse
import json
import os
import sys

from .handoff_store import Hold, MAX_REQUEST_BYTES, decode
from .quota_handoff import Registry


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--actor", required=True)
    args = parser.parse_args(argv)
    try:
        request = decode(sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1))
        result = Registry(args.registry).invoke(principal=f"uid:{os.geteuid()}",
                                              actor=args.actor, request=request)
    except Hold as error:
        print(json.dumps(dict(status="held", reason=str(error))))
        return 2
    except OSError:
        print(json.dumps(dict(status="held", reason="authority_unavailable")))
        return 3
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
