# Read-only Morgan Cloud status

This diagnostic is separate from Foo's native credential file and controller.
It performs only GET of the exact UIF authority HTTPS `/v1/status` route, through
the configured environment HTTPS proxy. It does not start, stop, claim, send,
renew, publish, mutate quota, or perform provider inference.

In the authorized Morgan Codex Cloud environment, use the environment owner's
HTTP-secret settings to provision `UIF_MORGAN_ACCESS_CLIENT_ID` and
`UIF_MORGAN_ACCESS_CLIENT_SECRET` with the already approved scoped Morgan
identity. Restrict both to the exact hostname built into the client, port 443.
Use network secret bindings, not plain environment variables, chat messages
or credential files. Run only in the intended managed runtime that receives
those placeholder bindings and normal environment HTTPS proxy. Do not unset
proxy settings or redirect the request. An environment republish does not
retroactively bind a running task.

From exact UIF source in that environment:

```sh
python3 -I -S -B tools/delivery/morgan_cloud_status.py
```

The command emits only role, stopped and coordinator epoch. Missing bindings
or a bypassed proxy cause a pre-request refusal; redirect, invalid content,
different role or ambiguous status is not a verified status. This verifies the
authenticated Morgan-role route from the invoking runtime, not its enrollment
provenance or global work status. Keep the operation receipt when proving the
specific runtime. Do not report this as a binding on a different existing task.
