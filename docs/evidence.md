# Evidence references

The foundation distinguishes source inspection, owner reports, scoped source acceptance and runtime effects.

## Verified source interfaces

Read-only inspection verified candidate `b8500ca73f23605e2aaeb7270ae4de36bbf16633`/tree `7ad44638e20942f1cfdcf04c61da49fcf99be064` clean in /home/user/src/offmenu-worktrees/consumer-cloud-b850-1105. It remains a suspended read-only lane. Interface details are in architecture.md.

The later independent scoped acceptance was supplied by the coordinating owner: [review receipt](https://windmill.aldous.info/run/01a1ad74-2798-5324-6a85-1215470681dd?workspace=offmenu), SHA256 `4d4f9cfc14fb11125c46e55abcf38bb5624ab79b3eb32b6f034b570a40fb21c4`; [invariant receipt](https://windmill.aldous.info/run/01a1ad76-5b0e-e78d-30d2-418c0eac2e25?workspace=offmenu). Private approved diff content is not reproduced here. This supersedes the local pending-review wording only within its accepted source scope.

The separate root handover is pinned by SHA256 `18f1b87c0da6aa8a7c1a35c4605815a63a1003c738ffaa7e2101bc3080d4fcaf` and final manifest `b490fafcf8afe4996375ad1b251d90b4f180d36ca71661d8cadcae37adf913ed`. It identifies root source `1a3c0df32dee1c10b1e366a8dd629c3c668bdc1a`/tree `45931f673bab6c6a2e3899d2b542e6ca814a6026`. This is not b8500ca, published product source or installed control.

## Durable requirements

- [UIF-161: material contract alignment](https://linear.app/maldous/issue/UIF-161)
- [UIF-120: native platform outcome](https://linear.app/maldous/issue/UIF-120)
- [UIF-122: shared contract ownership](https://linear.app/maldous/issue/UIF-122)
- [UIF-132: retained completed-native evidence](https://linear.app/maldous/issue/UIF-132)
- [UIF-139: OffMenu outcomes](https://linear.app/maldous/issue/UIF-139)
- [UIF-177: CQ controlled handover](https://linear.app/maldous/issue/UIF-177)
- [UIF-178: CQ read-only readiness](https://linear.app/maldous/issue/UIF-178)
- [UIF-179: bounded original-source assessment](https://linear.app/maldous/issue/UIF-179)
- [UIF-108: repository reconciliation/custody coordination record](https://linear.app/maldous/issue/UIF-108)

Project source snapshots marked owner-reported require current owner reconciliation before execution. No temporal job counts or private runtime payloads are maintained here.

## Remaining boundary

qualify_reviewed_source, its actual handoff schema and the selected source-outcome consumer must be checked together before temporary output is accepted. Existing reviewed-source support does not prove arbitrary subagent-output acceptance. Any correction belongs to its existing runtime owner with UIF-161 impact/review evidence, not a second implementation in this repository.

## Client-capability inventory evidence

- Existing obligation: [UIF-192](https://linear.app/maldous/issue/UIF-192), UUID `46dddb37-1497-4be5-8874-812f4f86932b`. This remains an aggregate partial requirement; only the finite inventory has been independently accepted.
- Source: `maldous/offmenu` commit `3540b2d4d5f4e96a518a63c832ccd6c3db809686`, tree `62efa45bb564affe4f1f1b2388bbb3447a14482c`. This is a named inventory baseline, not an installed/runtime pin.
- Frozen inventory SHA256: `1f52e20702f1faab8c82ddf9fb3688ef25bfa8cc9e456284386267fba0c1e76d`.
- Independent review SHA256: `852b899a535a5a8f304ba78b820ffc0af7143191e797e57e9dd22db8a96ab66d`; [durable finite review record](https://linear.app/maldous/issue/UIF-192/define-reusable-client-integrations-and-project-capability-gaps), comment `617ee572-e937-490d-a7af-26477584bc6a`.
- [Original submission](https://windmill.aldous.info/run/01a1ae0d-ab59-a4c5-163d-56af76bf8362?workspace=offmenu) and [terminal metadata/hash receipt](https://windmill.aldous.info/run/01a1ae0d-e68d-6d21-af54-723203ea5f1e?workspace=offmenu). Bounded source assertions passed; the hardcoded seven count is inaccurate because the script has six assert statements. No test suite, provider behavior or service activation was verified.

| Source member | SHA256 |
| --- | --- |
| `apps/web/server/service-config.mjs` | `bf79c76108caf4754950e89c1af52a43ef8d7ebaef4e47c839b8d8a2e9369bc7` |
| `apps/web/server/service-observability.mjs` | `5d40fa700e3e5814a2476b65cb920992c16ab0d9931770d9d2009e7cc778c3b0` |
| `apps/web/server/auth-runtime.mjs` | `b136b1879793e547f491cc4fb4605fb22fd91f9d499e4c5d32852cdb8dc6195d` |
| `apps/web/server/handler.mjs` | `8e753d34a86cf463c45c386f6a9381a1d78e36373076f1545403d0164428d480` |
| `apps/web/src/services-model.js` | `05d3279638586ad890de104504b40466f13cbdd1bbb87d4091f1962c77ed1ff9` |
| `apps/web/src/services-ui.js` | `0db7f4719d4244ce28bf8e26763ac477f948d21aa3be4f31c95aeb49ecd397f4` |
| `docs/third-party-client.md` | `885cf6371c4f7c0a41cdf61ac5aec66f9a902b29902f01b5f6a98207e6b21ff2` |
| `docs/maintainer.md` | `1cdebe739dd6c1429d7582656f74c4c3610501bb965ed23d37c4c1ffea70bb6c` |
| `netlify.toml` | `e577c36d93dae84b3c36c46e51e79138e187d2e492593ad34ec0ab0d9e77c3ac` |

Existing OffMenu developer-reference work remains with [UIF-184](https://linear.app/maldous/issue/UIF-184); CQ observability remains with [UIF-172](https://linear.app/maldous/issue/UIF-172), and CQ hosting/provisioning remains with [UIF-176](https://linear.app/maldous/issue/UIF-176) and its maintained RELEASE guidance. These are ownership links, not newly created blocking dependencies or permission for cross-project source edits.
