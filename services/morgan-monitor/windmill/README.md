# Windmill public SVG renderer

`morgan_public_render.ts` returns Windmill's documented rich-display
`{svg: "<svg>…</svg>", metadata: {...}}` shape. It uses the same public receiver
renderer as the web and HTTP MCP, without image generation or transcript access.
Its default is the explicitly chosen **compact MCP presentation**. That choice
does not assert allowance exhaustion. Callers can select O2, legacy hexagon or
focus and can pin an immutable snapshot revision.

## Fixed public read boundary

The sole resource field is `renderUrl`, whose only accepted value is
`https://morgan.aldous.info/render.svg`. The resource type definition is in
`renderer-resource-type.json`. There are no credentials, headers, arbitrary URLs,
filesystem paths, source snapshots or executable code among the caller inputs.
The wrapper sends a credential-free GET only, refuses redirects, imposes a
five-second timeout and a two-MiB response limit, checks the image MIME type,
rejects active SVG/remote references and requires the immutable revision marker.
Errors have fixed public messages; upstream error bodies never become results.

The returned metadata contains the snapshot revision, SHA-256 of the exact SVG
bytes, requested and rendered layouts, dimensions, and measured wrapper time.
If the public receiver supplies revision/digest headers, they must match. These
are artifact-pairing receipts, not proof that a TV displayed the image. Live
receiver responses without an explicit revision also render the first saved
immutable snapshot for their computed revision.

## Actual qualification and remaining work

Observed in workspace `offmenu`, 2026-11-09:

- The connector allowed creation of the `c_morgan_public_renderer` resource type
  and server-side per-user script/resource drafts.
- Script `u/admin/morgan_public_render` is deployed. Deployed-state readback
  matched the exact reviewed Bun source and confirmed `extraPerms: {}`.
- Resource `u/admin/morgan_public_renderer` is deployed, containing only the
  exact public render URL, with `extraPerms: {}` and no OAuth/account binding.
  Its initial deployment was blocked; after the user specifically approved
  this resource and read-only script, one retry succeeded. No alternate route
  was used to deploy it.
- Admin-only workspace settings could not be read through this connection.
  Draft authoring does not establish administrative privileges.
- Sixteen recently pinging workers advertised `bun`, but none advertised
  `nativets`. A native preview remained queued and was explicitly cancelled.
- Regular Bun was qualified through a preview of this script's input guard.
  It completed under the existing worker with no dependency installation and
  returned the expected invalid-options result in 0.038 ms of measured script
  time. This establishes runtime/guard execution only, not public SVG retrieval.
- A subsequent full preview resolved the deployed resource and performed the
  fixed-origin fetch. It returned the safe `renderer_unavailable` result after
  5005.253 ms, reaching its configured timeout while the public renderer was not
  yet serving an image. This is actual Windmill execution, not image-delivery
  success or a deployed-receiver latency measurement.

The checked-in target is therefore regular Bun. Do not add `//native` or switch
worker routing until an eligible native worker is independently qualified.
No repository filesystem mount or source-local renderer path has been proved on
the worker, so this wrapper uses the explicit public HTTP contract instead.

Remaining integration steps are: qualify the exact public receiver, execute the
deployed script with `$res:u/admin/morgan_public_renderer`, and inspect the SVG
plus its revision/digest receipt. Resource/script permissions must remain at
the authorized existing scope. This integration does not change the current TV
service or configure any domain, ingress or secret.

Verified deployed script:
https://windmill.aldous.info/scripts/get/u/admin/morgan_public_render?workspace=offmenu

Verified deployed resource:
https://windmill.aldous.info/resources?workspace=offmenu#/resource/u/admin/morgan_public_renderer

Guard-only Bun preview:
https://windmill.aldous.info/run/01a1becc-4719-ca92-6ad8-6f5505fb8389?workspace=offmenu

Configured public-fetch preview, timed out safely:
https://windmill.aldous.info/run/01a1bece-ac51-6d7e-6fc4-350f7bad4c28?workspace=offmenu

## Tests and latency measurement

```sh
node --test services/morgan-monitor/test/backend*.mjs
node services/morgan-monitor/test/benchmark-render.mjs
```

The benchmark uses synthetic public metadata and no network. On Foo Node
24.16.0, one 300-sample run after the shadow styling update measured compact
rendering at 0.103 ms p50 / 0.203 ms p95 and O2 at 0.496 ms p50 / 0.672 ms p95.
These are CPU measurements, not
Windmill queue latency, network retrieval latency, deployed Cloudflare latency,
or TV-display confirmation. Repeat after renderer changes. The wrapper reports
its own elapsed time when a real public request is successfully qualified.

References: Windmill's current script authoring guidance and
https://www.windmill.dev/docs/core_concepts/rich_display_rendering .
