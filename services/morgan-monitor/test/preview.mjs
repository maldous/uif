/** Loopback-only development harness. No external adapters, secrets or delivery.
 * Default = unconfigured. --fixture adds conspicuously labelled synthetic data.
 * SQLite shim exercises handlers, not a claim of Cloudflare deployment parity. */
import { createServer } from "node:http";
import { DatabaseSync } from "node:sqlite";
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { resolve, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { handleRequest } from "../worker.mjs";
const fixture = process.argv.includes("--fixture");
const port = Number(process.env.PORT || 8789);
const root = resolve(fileURLToPath(new URL("../public/", import.meta.url)));
class D1 {
  constructor() {
    this.sqlite = new DatabaseSync(":memory:");
    for (const f of readdirSync(
      new URL("../migrations/", import.meta.url),
    ).sort())
      this.sqlite.exec(
        readFileSync(new URL("../migrations/" + f, import.meta.url), "utf8"),
      );
  }
  prepare(sql) {
    const db = this.sqlite;
    const make = (params) => ({
      bind: (...p) => make(p),
      first: async () => db.prepare(sql).get(...params) || null,
      all: async () => ({
        results: db.prepare(sql).all(...params),
        success: true,
      }),
      run: async () => ({
        meta: db.prepare(sql).run(...params),
        success: true,
      }),
    });
    return make([]);
  }
}
const env = {
  DB: new D1(),
  SOURCE_CONFIG_JSON: '{"sources":[]}',
  ASSETS: {
    async fetch(request) {
      const p = new URL(request.url).pathname;
      const file = resolve(root, "." + (p === "/" ? "/index.html" : p));
      if (!file.startsWith(root + sep) && file !== root)
        return new Response("Not found", { status: 404 });
      if (!existsSync(file)) return new Response("Not found", { status: 404 });
      let body = readFileSync(file);
      if (extname(file) === ".html" && fixture)
        body = body
          .toString()
          .replace(
            "<body>",
            '<body><div style="padding:12px;background:#ffedb7;color:#5e3b00;font:700 15px Arial;text-align:center">SYNTHETIC TEST FIXTURE · Not current platform state</div>',
          );
      return new Response(body, {
        headers: {
          "Content-Type":
            {
              ".js": "text/javascript",
              ".css": "text/css",
              ".html": "text/html",
            }[extname(file)] || "application/octet-stream",
        },
      });
    },
  },
};
if (fixture) {
  const sources = ["cloud", "foo"].map((domain) => ({
    id: "test_" + domain,
    domain,
    aliases:
      domain === "cloud"
        ? [..."ABCDEF"]
        : Array.from({ length: 64 }, (_, i) => String(i + 1)),
    capabilities: domain === "cloud" ? ["process", "curation"] : ["process"],
    staleAfterSeconds: 3600,
    outageAfterSeconds: 7200,
    tokenEnv: domain.toUpperCase() + "_TEST_TOKEN",
  }));
  env.SOURCE_CONFIG_JSON = JSON.stringify({ sources });
  for (const source of sources) {
    env[source.tokenEnv] =
      "synthetic-loopback-only-test-token-" + source.domain;
    const observedAt = new Date().toISOString(),
      payload = {
        schemaVersion: 1,
        sourceId: source.id,
        eventId: "test1",
        sequence: 1,
        observedAt,
        health: "ok",
        coverage: {
          complete: true,
          observed: source.aliases.length,
          total: source.aliases.length,
        },
        sessions: source.aliases.map((alias, i) => ({
          alias,
          assignment: i < 3 ? "assigned" : "unassigned",
          observedAt,
          state: i === 2 ? "blocked" : i < 3 ? "running" : "unknown",
          proof: "process",
          topicKeys:
            i < 3 ? ["engineering", "testing", "review"].slice(i, i + 1) : [],
          blockerKeys: i === 2 ? ["awaiting_input"] : [],
        })),
        ...(source.domain === "cloud"
          ? {
              conversationTopics: {
                voice: ["monitoring_coordination", "status_display"],
                chat: ["status_updates"],
              },
            }
          : {}),
      };
    const result = await handleRequest(
      new Request("https://preview.invalid/v1/ingest", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: "Bearer " + env[source.tokenEnv],
        },
        body: JSON.stringify(payload),
      }),
      env,
    );
    if (!result.ok) throw new Error(await result.text());
  }
}
const server = createServer(async (req, res) => {
  try {
    const response = await handleRequest(
      new Request("http://localhost:" + port + req.url, { method: req.method }),
      env,
    );
    res.writeHead(response.status, Object.fromEntries(response.headers));
    res.end(Buffer.from(await response.arrayBuffer()));
  } catch {
    res.writeHead(500);
    res.end("Preview unavailable");
  }
});
server.listen(port, "127.0.0.1", () =>
  process.stdout.write(
    `Loopback preview http://127.0.0.1:${port}/ (${fixture ? "SYNTHETIC FIXTURE" : "unconfigured, no live telemetry"})\n`,
  ),
);
