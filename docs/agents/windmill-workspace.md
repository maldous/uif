# Windmill workspace and MCP routing checkpoint

Coordination checkpoint: 2026-11-12, against UIF source
`ecfe399acaf8c013a1fa2a68e2879a1fc330b687`. The existing coordinator reports a
fresh `foo_workspace_list_workspaces` result containing only
`{"id":"foo","name":"Foo"}`. This is the current caller's visible connection
inventory, not a census of every server workspace or proof of host execution.

## Select the actual current route

| Surface | Current scoped guidance |
| --- | --- |
| Generic Foo Workspace connection | Technical workspace ID `foo`, display name `Foo`. Supply `workspace_id: "foo"` to its workspace-scoped tools. |
| MCP endpoint | `https://windmill.aldous.info/api/mcp/w/foo/mcp` |
| Generic root-script invocation | `mcp__codex_apps__foo_workspace_run_script`, with `workspace_id`, `path` and nested `args`. The selected script path is `u/admin/foo_root_jobs`. |
| Fixed legacy wrapper | `mcp__codex_apps__windmill_foo_root_chat_foo_root_jobs` still advertises workspace `offmenu` and a disabled label. It has no workspace argument. Do not invoke it or add an invented workspace parameter. |

Discover each caller's actual tool surface and schema. A successful generic
connection does not retarget the fixed wrapper, change its grant or establish
its availability in another agent. Repair or republication of that wrapper
belongs to its existing owner and is not performed by this guidance.

The generic call shape nests the runner's actual parameters under `args`.
For example, a separately authorized status read uses the original job identity:

```json
{
  "workspace_id": "foo",
  "path": "u/admin/foo_root_jobs",
  "args": {
    "action": "status",
    "job_id": "<original-job-id>"
  }
}
```

The retained runner protocol declares `action`, `command`, `cwd`, `job_id` and
`timeout_seconds`, as applicable to the selected action. Confirm the deployed
Foo script schema and grant before dependent use; the private root execution
grant in `foo` is not qualified by this connection-inventory read. There is no `run_as`
or policy-version/hash argument in that contract. The example submits nothing;
it grants no host command, credential placement or persistent access. Follow
current task authority and the discovered schema. Record an admitted operation's
job ID before submission and reconcile that same ID after an uncertain outcome.
A wrapper response alone is not a terminal host receipt.

## Keep the different identities separate

- The connected routing workspace is `foo`. Historical workspace `offmenu` and
  original job/receipt identities remain provenance; do not rewrite those events
  or use the fixed wrapper as an automatic fallback for new work.
- The desired offline `workspace.id: null` and `requires-explicit-target` state
  describe a separately selected isolated recreation target. They are not the
  connected workspace ID and must not be filled with `foo` to match branding.
  The accepted recreation guard preserves `foo`, `offmenu` and `admins`.
- OffMenu product repositories, scripts, data and compatibility service names
  have their own owners. Workspace branding does not select a rename or transfer
  their ownership. Separately, the current UIF requirement makes active shared
  infrastructure filenames and service/runtime bindings project-neutral. That
  source/deployment migration remains with its existing integrator; product
  identities may remain within explicit optional product boundaries.
- Historical `offmenu-wm` and credential-destination observations do not qualify
  the current Foo execution account, credential placement or native roots.
  Verify those bindings through their existing owner and permitted evidence;
  do not infer a renamed SSH account or a new credential grant.

## Source alignment and qualification remain separate

At the stated source baseline, `config/windmill/desired.json`, its offline
validator and the root-interface expectation in `config/white-room.json` still
contain the older transport tuple. The existing recovery coordinator/integrator
owns their migration assessment, source changes, tests and integration. This
checkpoint does not change or accept those declarations and does not qualify
the OffMenu-only bridge through a generic workspace-name substitution.

`services/morgan-monitor` is absent from this accepted baseline. Its candidate
guidance correction remains with the existing PR21 writer through Morgan; do
not copy that candidate source into this checkout or describe PR21 as adopted.

Preserve the original bytes of `config/windmill/observed-2026-11-09.json`,
`config/windmill/dispositions.json`, `config/windmill/source-provenance.json`
and the dated receipts. Historical exports alone do not qualify active use.
New current evidence belongs in its own reviewed observation; older observations are not
rewritten to claim they captured `foo`. The dated
[white-room discovery](../white-room/discovery.md) and
[instruction-reference feasibility](windmill-instruction-feasibility.md) retain
their historical scope and now point to this checkpoint.

The current connection inventory does not establish the actual Foo credential
binding, native roots, host protocol, runtime state, lock closure, permissions,
execution capacity or recovery readiness. Those checks remain scoped to the
selected operation and existing owner; unrelated authorized cloud work can
continue. No service, backend source, security control, grant, deployment or
live invocation is changed by this documentation.
