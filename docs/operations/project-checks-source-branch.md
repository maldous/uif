# Exact-branch deterministic checks on Foo

Use the deployed `u/admin/project_checks` script in the connected Windmill
workspace `foo`. Discover the caller's actual tools and read the deployed source
and schema before use. This is deterministic execution, not a Foo Codex session,
a publisher, an ownership transfer or production deployment.

## Normal source flow

1. Reconcile current Linear scope, owned paths, active claims and original effects.
   Work on an isolated project branch based on the selected accepted source.
   Preserve dirty or divergent owner checkouts and accepted test receipts.
2. Commit the bounded change to the correct GitHub project branch and verify the
   remote commit/tree. Native HTTPS Git credentials and the connected GitHub app
   are separate capabilities. An authenticated connector may publish source when
   native push authentication is unavailable; do not borrow credentials or claim
   that a connector commit proves native push access.
3. Read the deployed helper's `catalog` for the selected project. Then call
   `discover` with the exact `source_branch` and a new `operation_id`. Inspect its
   returned Windmill operation with `get_run`. Discovery is source metadata, not
   a successful build or readiness pass.
4. Call `run` with the same selected branch, the discovered full immutable
   `expected_commit`, an explicitly supported `target` and a new operation ID.
   `source_branch` names a branch in the helper's allowlisted project repository,
   not a local path, arbitrary repository or unpinned revision. A moved branch
   refuses the run rather than silently testing different source.
5. Inspect the returned `windmill_job_id` using Foo `get_run`. If the host outcome
   is not terminal, call `status` with the same project and original operation ID,
   then inspect its returned Windmill job. Never resubmit `run` after uncertainty.
   Report the inner target result and its commit/tree, steps and retained receipt,
   not merely a successful wrapper or a running root job.

For the generic connected route, supply `workspace_id: "foo"`,
`path: "u/admin/project_checks"` and put the script parameters under `args`:

```json
{
  "action": "run",
  "project": "uif",
  "source_branch": "docs/example-bounded-change",
  "expected_commit": "FULL_40_CHARACTER_REMOTE_COMMIT_SHA",
  "target": "check",
  "operation_id": "unique-bounded-check"
}
```

The example is a template, not a submitted or reusable operation. Use the actual
full hexadecimal commit, an existing branch and an unused operation identity.
For `status`, omit `source_branch`, target and setup arguments.

## UIF targets and acceptance limits

The deployed catalog inspected on 16 November 2026 exposes:

| Target | Declared work |
| --- | --- |
| `check` | Environment-policy conformance and offline Windmill declaration validation |
| `test` | Windmill, White Room, agent-policy and delivery unit suites |
| `compile` | Python compilation of source, tools and tests |
| `build` | Wheel build with `--no-deps --no-build-isolation` |
| `environment` | Bounded execution-environment assertions |

UIF has no declared `lint` command in this catalog. Unsupported targets fail
closed; do not invent one or relabel compilation as lint. Reuse accepted checks
at their valid exact bindings and select only checks needed for the new change.
Source/format guidance changes do not justify replaying unrelated product suites.

The helper uses a fresh isolated clone and verifies the selected commit/tree and
initial cleanliness. It does not update the project owner's existing checkout.
Declared test steps run as the checkout user, not root, with bounded CPU, memory,
process and runtime limits. The execution sandbox is not adversarial separation
from every process of the same host user. Its private network namespace cannot
be used as a probe of services on the host loopback interface.

`install_locked=true` permits only the selected catalog's declared isolated
setup. It is not an arbitrary dependency installation, toolchain download,
credential grant or deployment permission. Default to false when no declared
setup is needed. Missing build prerequisites remain a specific target failure,
not permission to relax the environment.

A terminal inner `passed` result proves only that target at its stated source.
It does not clear native gates, release claims, accept a whole issue, merge,
activate a runtime, establish provider allowance or prove unattended BAU. Preserve
existing independent review, integration/publisher custody and project holds.
Private source, output and receipts remain in the helper's original per-operation
custody; do not replay or delete them to obtain a cleaner status label.
