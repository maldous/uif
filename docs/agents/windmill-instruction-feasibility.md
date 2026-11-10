# Windmill/MCP instruction reference feasibility

## Verified interfaces, 2026-11-10

The connected Foo MCP `get_instructions(subject="script", language="python3")`
response explicitly supports an input schema top-level `prompt_for_ai` string
for instructions to an AI choosing inputs. It says to preserve this field when
rewriting a schema. The supported `list_workspace_items` metadata read confirmed
these existing deployed identities in workspace `offmenu`:

- `u/admin/foo_root_jobs`: owner-only Foo root transport; summary says disabled
  pending host protocol qualification. This is metadata, not a live health test.
- `u/admin/offmenu_delivery_dispatch`: typed dispatch with exact reviewed-source
  identity validation and immutable job IDs. No invocation was performed here.

The root tool currently exposes action, command, cwd, job_id and timeout_seconds;
it has no declared policy-version/hash parameter. Do not pass invented arguments.
The existing repository qualification and API declarations are evidence of
proposed/recovered contracts, not proof of current deployment or semantic policy
loading. The Windmill documentation search for prompt_for_ai returned no matching
page; the support claim above comes from the actual connected authoring guide.
No unsupported endpoint, credential read or denied host-shell source was used.

## Feasible advisory reference

A future reviewed schema change can include a short prompt_for_ai reference:

> Read the target repository's AGENTS.md and local
> docs/agents/uif-worker-policy.md before selecting inputs. Bind the current
> repository/revision, task owner, policy version/hash and permitted operation
> in the existing task handoff. Keep native/cloud availability independent of
> Foo Codex quota. Follow the existing operation schema and reconcile the same
> job identity before retrying uncertain work.

This is proposed text, not deployed configuration. Do not paste private task
history, secret values or raw datasets into shared schema descriptions. A path
on Foo is not available automatically to a cloud caller or Windmill worker;
provide the readable pinned policy through an authorized local copy/task input.
A reference alone neither loads the file nor proves the model followed it.

## What would constitute enforcement

A supported dispatcher boundary would need to validate a pinned policy identity
and repository/operation binding against trusted configuration before effects;
reject missing, stale or mismatched identities; record that validation in the
operation receipt; and cover every relevant dispatch route. Merely allowing a
caller to self-assert the current hash is not enforcement. Even such a mechanism
can enforce declared machine-checkable rules, not prove all prose was followed.
Existing ownership, resource, access and idempotency checks still apply.

That requires a separately reviewed implementation in the actual supported
runner/dispatcher contract, negative tests, authorized deployment and live receipt
readback. It must not become an added product-delivery gate or a way to bypass
an existing denied action. This investigation changes no production runner,
service, authentication, permissions, credentials, scheduler or deployment.

## Current outcome

Portable repository policy copies and an offline consistency checker are prepared.
They validate instruction distribution only. No universal MCP enforcement,
Windmill policy schema deployment, runtime adoption or worker compliance is claimed.
The prior denied privileged host-shell export remains untouched. Existing
Windmill migration and qualification work retains its original owner and holds.
