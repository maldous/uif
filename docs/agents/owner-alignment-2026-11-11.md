# Owner-driven operating-guidance alignment

Observed 2026-11-11 after the policy 1.2.1 rollout. This is a revision-scoped
source checkpoint, not a live work queue, an agent assignment or a runtime
readiness claim. Revalidate current branches, owners and operation receipts.
Preserve the earlier [adoption checkpoint](adoption-2026-11-11.md) as historical
evidence; its pending 1.1 candidates are not the later accepted source status.

## Verified source baseline

The following selected branches carried identical policy 1.2.1 bytes and matching
repository manifests when inspected:

| Project | Selected source | Accepted revision |
| --- | --- | --- |
| UIF | `develop`, PR 30 | `73e0b25f4e5604aa23208baba442541e43cd7326` |
| ANQR | `develop`, PR 6 | `2f7a1cb8957c97ac5bbe6edeefc474a7f1e0cc00` |
| OffMenu | `develop`, PR 220 | `349dbf9ff37d5bf9de15695d70391234b0f6f8ee` |
| CIX product | `develop`, PR 10 | `92610987853632dec8736b0cd9e5791da0e033c4` |
| Circular Quay | PR 1, `feature/initial-implementation` | `6c0315e49fdd5f907dc3e4a95a353b75d42023e1` |

Policy 1.2.1 SHA-256:
`979d77a79dd19b1dc4604463bcee7b9b34ebfe6109c39cb20e6ed9bd55381052`.
Circular Quay PR 1 remains open and unmerged; its empty `develop` initialization
tree is not the bootstrap guidance source. The CIX operations surface requires
separate owner reconciliation and is not covered by CIX product alignment.

## Clarification in this change

Policy 1.2.2 clarifies project-owned delivery, direct available authorized
GitHub/Foo MCP work, inter-agent coordination and continuation. Morgan retains
cross-project priorities/allocation-conflict coordination and Foo Codex session
orchestration. Routine steps do not acquire a new Morgan approval gate. Existing
project review, integration, admission, resource and publication controls remain.

Status reporting shares known verified events first, then obtains missing or
changed details from current owners. Prepared, published, merged, acknowledged
and actually adopted/executed states remain distinct. No shared allocator,
automatic status transport or deployed enforcement is asserted by this change.

## Distribution and adoption evidence

The local policy and manifest identify this checkout's selected source. Adopt
the same reviewed bytes through each project's existing owner and integration
path, using the Circular Quay PR 1 branch during bootstrap. Verify publication
and merge outcomes separately; do not infer five-project rollout from a UIF merge.

Current project owners must read the accepted entrypoint/policy and pass its
version/hash and local contracts to children. Record actual acknowledgments and
selected checkout/source adoption in the existing coordination record. A sent
handoff or merged source does not establish a running worker, available tools,
Foo session readiness, recovered ownership, BAU or continuous delivery. Missing
owner/capability evidence remains explicit and does not block unrelated work.
