# Discovery and remaining qualification, 2026-11-09

These are dated observations, not durable readiness assertions. No service was
started, stopped, reconfigured or migrated. Do not reuse them after the configured
freshness window as admission evidence. UIF-247 remains the broader bootstrap
outcome: https://linear.app/maldous/issue/UIF-247/unify-cross-project-agent-bootstrap-contracts

## Current routing checkpoint, 2026-11-12

The coordinator's fresh connection inventory reports only workspace `foo`, display
name `Foo`, for the current generic Foo Workspace caller. Use the
[Windmill workspace guidance](../agents/windmill-workspace.md) for its endpoint,
generic call shape and separation from the fixed `offmenu` wrapper. That wrapper
has no workspace argument and is not repaired or qualified by this documentation.

The November 9 observations below remain historical evidence. In particular,
their tool alias, workspace, identity receipts, worker snapshot and unit state
are not current admission evidence. Source/configuration/validator migration
assessment remains with its existing recovery coordinator/integrator; this
documentation does not adopt that migration or establish live readiness.

## Historical interfaces and configuration, 2026-11-09

- Remote UIF develop initially e09a66585c2091fbb86b353c5bd630a62450aff8
  contained only AGENTS.md and README.md. The separate README owner subsequently
  published develop dab32361acc8df0004e0f8d1a9860642d568c963 and reported a
  clean fast-forward of canonical Foo. This code is additive to that baseline.
- Discovered MCP root tool is
  `mcp__codex_apps__windmill_foo_root_chat_foo_root_jobs`, deployed Windmill
  `u/admin/foo_root_jobs`, workspace `offmenu`. Supported parameters are action,
  command, cwd, job_id, timeout_seconds; **no run_as**. Host identity receipt
  returned uid 0, hostname foo, cwd /. Repository/tests use `runuser -u user`
  with explicit HOME, not root. The bridge's protocol is `foo-root-exec-v1`.
- Deployed root script source SHA-256:
  ac933811fe4b250e025afb30d5be8d8a9f2afb587d18027e90fdeaa18f5e26ba.
  Its source declares `ENABLED = True` and pins paramiko 5.1.0 / wmill 1.850.0.
  The tool/summary still says disabled pending qualification. **That description
  is stale**: original terminal identity receipts prove this read-only invocation
  completed. This does not establish every root operation is qualified.
  SSH key values were neither read nor copied. No credential or endpoint renamed.
- OffMenu Chat exposes mutation-shaped tools despite its read-only label.
  Discovery used only listing/reading. Do not infer mutation authority from schema
  presence. Exact aliases are environment bindings; a change needs discovery and
  review, not blind substitution or accidental rename.
- Windmill listing returned 16 default workers with recent pings and python3 among
  tags. This is a bounded snapshot, not guaranteed available job capacity.
  Deployed paths include `u/admin/offmenu_delivery_dispatch`,
  `u/admin/foo_jobs_20261027`, `u/admin/offmenu_platform_checkpoint` and
  `u/admin/offmenu_platform_check`. Listed schedules include
  `u/admin/offmenu_platform_health_15m` and
  `u/admin/offmenu_platform_verify_daily`; listing does not establish enabled state.
- Stokowski unit `offmenu-stokowski-current.service` reported inactive/dead,
  Result=exit-code, MainPID=0, NRestarts=201. Its installed binding selected source
  5b8ca36e3a212d60db7b74bb8ccb37e3993bb01e, a cloud GitHub owner and two project
  routes. No inference of active delivery or permission to restart follows.
- OffMenu's current delivery runbook declares Python 3.12, a pinned delivery
  environment, LangGraph SQLite checkpoints and LangChain core integration.
  It explicitly separates the cloud authenticated publisher from Foo, which lacks
  an independently configured GitHub API grant. Do not copy cloud credentials to
  Foo or call local availability full publication continuity.

## Historical project dependency discovery, 2026-11-09

Read-only canonical checkout inventory, before the independent UIF sync:

- UIF: develop 88ae772d7277c9783729aaf397ab2de1d7ab2e79, clean, README only.
- OffMenu: develop 10800ba65bb260606a44745ae57fd64150d0853b, dirty, Python and
  JS manifests/lockfile plus many config binding declarations. Preserved unchanged.
- CIX: develop e7ea2923e9f81d2812ea3cb893edd925e51548ed; Rust workspace,
  Python/Node bindings, Go network and gRPC manifests discovered.
- CIX operations: develop c070893c0dae3cc58efc13baecadd29b24b2132d;
  Python/Rust/Go/Node manifests, Elixir Symphony integration, service declarations
  and archived contracts. Active versus historical needs further qualification.
- Lowercase `/home/user/src/circularquay` and `/home/user/src/anqr` did not exist
  in the narrow probe. This is **not** absence of those projects; correct paths,
  aliases, repository identities and effective bindings remain unresolved here.

Discovery is partial. Null declarations and completeness=false preserve those
unknowns. In particular: effective data mounts/access modes, project/customer
services, quota evidence, active Git runners/webhooks, exact installed lockfiles,
checkpoint state, pending external operations and authoritative ownership/fencing
remain unqualified. No raw data/database bodies or credential values were read.

## Historical validation boundary, 2026-11-09

The same synthetic comparison suite runs in an isolated cloud workspace and a
new user-owned temporary directory on Foo through the actual root MCP transport.
The final run is reconciled through its original job ID before success is reported.
This validates code/interpreter compatibility and non-destructive fixture behavior,
not actual service restart, lost-process recovery, lease transfer or distributed
publication. Host temporary test artifacts are retained; canonical sources/data
are not overwritten. Exact final results and reviewed commit belong in the PR.

Next work is the authenticated adapters and project-specific contract completion,
then integration with the existing harness's atomic ownership/reconciliation
controls. Actual planned/unplanned worker/coordinator interruption and bidirectional
handoff remain not run. Those gaps must not be hidden by a green synthetic suite.
