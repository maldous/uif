# Verified UIF interfaces and evidence pointers

Managed policy alignment: **2.0.0**. This source copy does not replace an installed
user skill automatically. Revalidate current source, authority and grants before use.

## Current runtime mapping observed 2026-11-14

The UIF owner reported a host read in turn `01a1d77a-d045`, corroborated by
current host inspection: systemd unit `stokowski-foo.service` executes the
native-core `launch.py` with `workflow.yaml`; the launcher calls
`run_module('stokowski')`. Installed package metadata identifies
`Sugar-Coffee/stokowski`. These are observed component identities, not absolute
filesystem paths, a pinned source revision, or proof of delivery readiness.
Resolve actual paths and the deployed revision from the current unit and
installation before using them; do not construct paths from these names.

The current connector workspace is `foo`. For Windmill evidence, use the
available read-only run-list/detail tools with `workspace_id="foo"`, checking
their current schema and response limits. A workspace identifies an execution
surface, not a project's owner or the scope of every job.

Use this dated mapping to discover the current service, then verify it afresh.
Do not use the historical OffMenu unit or workspace below as the current target
without independent evidence. The previous command examples remain historical;
this update does not establish start/stop privilege, graceful drain semantics,
or authorization to operate the new unit. Apply current-interface checks and
the skill's distinction between conversational controls and automatic enforcement
of already-authorized durable desired state. Routine recovery needs no fresh
user prompt. Do not invoke the
launcher or `run_module` to obtain a report.

## Implementation readback: 14 November 2026

The implementation owner used the generic Foo Workspace route with
`workspace_id: "foo"`, `path: "u/admin/foo_root_jobs"` and nested `args`.
The connection-status original job
`uif-policy-implementation-read-20261114-01` returned `not_found`.
The original host-read job `uif-policy-host-binding-20261114-01` returned
`succeeded`, exit 0, no truncation, hostname `foo`, uid 0; it observed
`stokowski-foo.service` active/running, service user/group `user`.
original receipt retained in the authorized Foo evidence surface.

Observed ExecStart uses
`/opt/offmenu-stokowski-current/venv/bin/python` with the native-core
`/opt/uif-stokowski/native-core-20261113-a1/launch.py` and
`workflow.yaml`; working directory is that native-core release. These are
dated observed bindings, not a requirement to rename the interpreter path or
permission to operate the unit.

The subsequent original source-read job
`uif-policy-native-source-20261114-01` observed launcher SHA-256
`27cd0f2f3f9b51d3bdbd17cbe4d43a6e76e142f513fc1a40dfcd9a054667d5b7`
and workflow SHA-256
`375c0c3d31b0a238bd22d14a1b526ef0e499c16894fcbb611c00bcbdbcbf2854`.
The launcher loads native Stokowski and keeps the existing Linear credential in
controller memory while excluding it from the child environment.
original receipt retained in the authorized Foo evidence surface.
No start, stop, restart, install, credential or capacity change occurred.

Use current generic Foo Workspace when that route is verified and authorized.
The historical fixed root wrapper has no workspace parameter and must not be
silently retargeted or used as a fallback around missing access. Root script
inputs remain `action`, `job_id`, `command`, `cwd`,
`timeout_seconds`; no invented `run_as`, generation or policy-hash field.
Reconcile an uncertain host job using its original ID and workspace.

These reads establish host identity and those observed files only. They do not
prove current claims/fencing, quota, effective resource capacity, useful
multi-project delivery, checkpoint recovery or automatic mode transition.
The 2.0 source adapter's installed integration and end-to-end evidence remain
separate qualification outcomes. Retain the historical references below without
using them as current commands or authority.

Morgan subsequently verified an existing enabled ten-minute off-Foo coordination
heartbeat and cloud-thread read/send capabilities. Reuse that wake; it is not
an atomic claim/effect-fencing or controller-election interface. Neither the
heartbeat nor the observed Cloudflare monitor/D1/BridgeQueue inventory proves
the missing independent ownership authority or grants its use. The existing
custodian and source writer retain their distinct implementation scope.

## Historical source and freshness: 2026-11-04

These interfaces were reported as verified from the deployed service definition and existing operator implementation by the BAU coordinator on 2026-11-04. Source: coordinator task `01a130cd-91c1-73da-9978-889018a100dd`, turn `01a1a87d-585e-759a-b2dd-c1404d9124c7`, result message `msg_0382918b42046e6a016aeba557be2487d0b9652b3cdf02ac7f`.

The sole current adoption/live integration owner at that observation was Root task `01a19399-d6b3-7a60-add3-f512f721988d`. Revalidate identity before routing; ownership may change. This pointer does not authorize messaging or steering the owner outside an authorized workflow.

The deployment host is Foo. Paths below are on that host, not necessarily on the assistant's cloud filesystem. Use the existing authorized environment route. Do not assume a matching path on another machine is the live service.

## Historical service-control examples

At the 2026-11-04 observation, the verified service unit was `offmenu-stokowski-current.service`. Its legacy OffMenu name does not define the scope of the multi-project UIF platform.

Only for an explicit user-directed start or stop, with the documented privilege path available:

```bash
sudo /usr/bin/systemctl start offmenu-stokowski-current.service
sudo /usr/bin/systemctl stop offmenu-stokowski-current.service
```

These are the existing operator implementation's commands, not commands executed to validate this skill. Do not change sudo policy, install a replacement unit, substitute a similarly named service, or bypass a denied privilege request. Confirm the unit's current target and stop semantics before acting; the recorded commands alone do not prove graceful drainage or cancellation of in-flight work.

Read-only control status:

```bash
/usr/bin/systemctl show offmenu-stokowski-current.service \
  -p ActiveState -p SubState -p MainPID -p Result -p NRestarts
```

Read control state before and after a requested operation. A command's exit code or acceptance is insufficient if destination state remains unknown. Inspect an uncertain original operation before retrying.

## Historical read-only delivery evidence

- **Installed binding:** `/etc/offmenu-stokowski-current/current.json`. Resolve the currently installed runtime and revision from current binding evidence rather than assuming this reference's runtime is still selected.
- **Runtime observed at source time:** `/home/user/src/offmenu/.delivery/stokowski/current-owner-foo-local-protected-bf9`.
- **Native activity:** read that runtime's `capacity-policy.json`, resolve its `symphony.pool_path`, and open the SQLite database in URI `mode=ro`. Inspect the actual schema and lifecycle semantics before counting; do not invent SQL table or state names. Never open in a mode that could create a missing database. Do not run checkpoints, migrations, updates, deletes, repairs, or lease cleanup to obtain a report.
- **Windmill activity:** use the available `list_runs` connector with `workspace_id="offmenu"` and `running=true`. The tool returns compact, limited recent metadata; use a returned run ID with the read-only run-detail tool when its state matters. Do not interpret a truncated or bounded list as a complete total, and do not classify all workspace jobs as one project's coding work.
- **Adoption evidence:** `/home/user/src/offmenu-assessments/native-platforms-20261101-01a19399/harness-pivot-receipt.json`. Treat it as a dated operation receipt, not proof of current deployment without binding and live-state corroboration.

Read-only evidence collection does not require dispatching a job, running a flow, creating a worker, updating an issue, or touching queue state. If only the read-only Windmill connector is available, report that coverage and the unavailable host/native evidence rather than pretending the whole service is verified.

## Historical documentation caveat

The coordinator identified `docs/development/stokowski-current-service.md` as an old proposal with stale limits and rollback wording. Do not copy it wholesale or use it as current control policy.

Current framework reconciliation was reported in `delivery-architecture.md`, `native-platform-harness.md`, and `project-aware-intake.md` at owner-reported revision `56f6742b3`. This revision did not resolve through the GitHub connector during skill preparation; do not treat it as a verified published commit. The exact current operations-document pointer was still awaiting confirmation from Root. Discover those files in the verified repository rather than inventing a URL or directory. Revalidate the latest authoritative operations documentation before a live control action.

Do not freeze transient job counts, restoration status, reviewer verdicts, installed revisions, capacity limits, or project eligibility into this skill. Read them afresh. A running service proves neither useful coding nor review, publication, recovery, or refill.
