# Windmill connector consumers, 11 November 2026

Dated evidence for
[UIF-211](https://linear.app/maldous/issue/UIF-211/retire-obsolete-offmenu-mcp-connector).
Read with the [identity record](connector-identities-2026-11-11.md).
These observations do not represent a complete live client inventory or cutover.

## Demonstrated current capability needs

- **UIF receipt reconciliation:** the general Foo MCP reader retrieved immutable
  Windmill receipts through `get_run`. Scoped Root `status` separately retrieved
  a native job receipt. Both capabilities were used; interchangeability is unproven.
- **ANQR current repository work:** its execution owner confirmed scoped Root
  `run/status` use, with no current need for general `get_run/list_runs`.
  Existing native job `anqr-canonical-fastforward-20261111-01` is succeeded,
  exit 0, untruncated. The saved terminal receipt was independently read.
- **CIX current custody work:** its execution owner confirmed scoped Root
  `run/status` use, with no general-reader call for this work.
  Existing native job `cix508-ref-delta-20261111-h` is succeeded, exit 0,
  untruncated. Its saved terminal receipt was independently read.
- **Morgan monitor active lane:** its owner reports a persistent supported local
  Codex daemon RPC collector, supported thread observations and Cloudflare
  publication. Current graph/layout work uses its own filesystem and Cloudflare,
  not general Foo MCP or scoped Root. This does not establish the binding used by
  a separate service-repair operation.

Each statement applies only to the described work and observation time.
It is not a project-wide promise, a transferred permission or an installed
connection identity. Other clients remain unclassified.

## Immutable historical receipt retrieval

The general reader successfully retrieved these original receipts on 11 November
without replaying their commands:

1. [UIF-192 metadata receipt](https://windmill.aldous.info/run/01a1ae0d-e68d-6d21-af54-723203ea5f1e?workspace=offmenu):
   `u/admin/foo_root_jobs`, native job
   `uif192-verify-metadata-20261105-2339`, succeeded/exit 0.
2. [UIF-193 publication-failure receipt](https://windmill.aldous.info/run/01a1ae08-011d-af63-d7c9-33370de8f76c?workspace=offmenu):
   `u/admin/foo_root_jobs`, native job
   `uif193-push-worker-20261105-2330`, failed/exit 1.
3. [UIF-102 legacy-runner receipt](https://windmill.aldous.info/run/820eae30-ddcc-5abf-b78f-9c08dccac3f4?workspace=offmenu):
   `u/admin/foo_jobs_20261027`, native job
   `offmenu-d72d6763b2cb3fedd42921783bb94b3429a2cfba621dc8e9`,
   succeeded/exit 0.

The outer retrieval succeeded for the failed publication too. That does not turn
the original host failure into successful publication. Original operation IDs,
outcomes and history are preserved. These are historical source/effect records,
not fresh test results or proof of a current repository head.

Scoped Root `status` returned `not_found` for the unused probe
`uif211-continuity-noexec-20261111-01`, without a command. It then returned the
existing `f-uif-source-preflight-20261109-01` receipt. All seven receipt fields,
including output, exactly matched the saved original November 9 result.
The output SHA-256 is
`68047e0d1cdb30b462a1f2a22ba1a2b44f08152a4494109c3056313ff7ced838`.
This proves bounded same-ID historical retrieval, not interruption recovery,
all-consumer continuity or current source qualification.

## Visible workspace schedules and callers

The current visible workspace listing requested flow, app, schedule and trigger
types. It returned two schedules and no flows, apps or triggers. Deployed readbacks
confirm both schedules remain disabled:

- `u/admin/offmenu_platform_health_15m` targets
  `u/admin/offmenu_platform_check`, mode `health`, maximum 100 items.
- `u/admin/offmenu_platform_verify_daily` targets the same script, mode
  `verify`, maximum 100 items.

Do not reactivate these schedules as an incidental rollback.

A bounded latest-20 run listing for each runner exposed recent November 11 Root
calls and retained legacy history, whose latest returned entry was November 3.
The creator field was `admin`; it did not expose a calling App, installed
connection or OAuth grant. A run list is not a host-job census or proof of idleness.

## Acceptance limits and next evidence

The existing source/configuration inventory and static dependency scan remain
useful historical evidence, but literal reference absence cannot clear dynamic
callers. A live MCP consumer existed despite no literal incoming reference to its
runner in that earlier scan.

Required missing evidence remains the exact installed old/successor pair,
effective access and complete consumer dispositions. Any selected successor must
preserve required current status, successful and failed historical receipts,
recovery/checkpoint metadata and safe same-operation continuation within the
existing authorization. These are the original UIF-211 acceptance requirements,
not new blanket delivery gates.

This documentation changes no connector, workspace, grant, service, schedule,
credential, source selection or operation outcome. It does not claim deployment,
retirement, complete least privilege or completion of the four UIF-211 actions.
