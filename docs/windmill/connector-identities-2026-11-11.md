# Windmill connector identities, 11 November 2026

This is a dated, non-executable evidence record for
[UIF-211](https://linear.app/maldous/issue/UIF-211/retire-obsolete-offmenu-mcp-connector).
It is not an installed-connection registry, deployment declaration or permission
to remove a connector. UIF-211 remains Backlog. See the
[consumer record](connector-consumers-2026-11-11.md) and existing
[Windmill qualification contract](qualification.md).

## Verified package and App identities

The following four private, user-scoped packages were returned by the owned-plugin
listing. Their current file lists were exhausted and the exact returned manifest
paths were read. These are package/release/App identities, not account-specific
installation, OAuth grant or credential identities.

### Standalone Root package

- Package: `plugins_6aee4b8ff90081918a6b237c75e8aa53`.
- Technical name: `windmill-foo-root`; display name: Windmill Foo Root.
- Version: `0.1.1`.
- Current release: `pluginrel_6af09380eb8c819195f235532f419752`.
- Its `.app.json` binds `windmill-foo-root-chat` to
  `asdk_app_6af091440f10819193fe92a9fef5e82e`.
- Its MCP server key remains `windmill-foo-root`.
- Declared OAuth base/default scopes are
  `mcp:scripts:u/admin/foo_root_jobs` and `mcp:runnables_as_tools`.
- The live exposed scoped tool supports `run`, `status` and `cancel`.
  These capabilities can execute commands as root; they are not a sandbox.
  This evidence pass invoked only `status`.

### App-backed Root package

- Package: `plugin_asdk_app_6af091440f10819193fe92a9fef5e82e`.
- Technical name: `dev-6af091440f10819193fe92a9fef5e82e`.
- Display name: Windmill Foo Root Chat; version: `1.0.0`.
- Current release: `PluginRelease_0a612769c9288191b547b0f4a3d9fcd6`.
- Its `.app.json` binds to the same Root App ID:
  `asdk_app_6af091440f10819193fe92a9fef5e82e`.
- The App manifest does not expose effective OAuth grants or an installed
  connection ID. The standalone package's declared scopes do not independently
  prove the App's actual granted permissions.

### Standalone general reader

- Package: `plugins_6ae081668e4881918eca5c057675f129`.
- Technical name and MCP server key: `windmill-offmenu`.
- Current display name: Foo MCP; version: `0.1.1`.
- Current release: `pluginrel_6af132935b68819181fb36c13fb861da`.
- Declared OAuth base/default scopes are `mcp:all` and `mcp:read_only`.
- The old Windmill OffMenu display name and version `0.1.0` belonged to this
  same package identity. The display change does not establish retirement or
  migration to a different installed connection.

### App-backed general reader

- Package: `plugin_asdk_app_6ae0888708808191b9d58f2a1a0907db`.
- Technical name: `dev-6ae0888708808191b9d58f2a1a0907db`.
- Current display name: Foo MCP; version: `1.0.0`.
- Current release: `PluginRelease_a87759689f88819199eb55e2e8c674f7`.
- App ID: `asdk_app_6ae0888708808191b9d58f2a1a0907db`.
- Package, App and release identities match the earlier Windmill OffMenu Chat
  record. This is the same-identity renamed reader, not a newly proven successor.
- The App manifest does not reveal an installed connection or effective grant.

## Endpoint and capability boundaries

Both standalone MCP configurations retain the same endpoint:
`https://windmill.aldous.info/api/mcp/w/offmenu/mcp`.
Their headers are empty and they declare OAuth dynamic client registration.
No credential values were retrieved.

The technical workspace remains `offmenu`. Shared project usage and a Foo display
name do not authorize changing that workspace, deleting its history or migrating
credential bindings. The current tool catalog showed a general reader surface and
a separate scoped Root surface. Catalog presence and requested scopes are not
proof of effective access, least privilege or authorization to invoke mutations.

The Root-only interface is not demonstrated to replace general historical
Windmill receipt retrieval. Conversely, the general reader's display description
does not establish unrestricted shell access. Keep the two capability statements
separate.

## Missing installed identity

The following remain unknown:

- The exact account-specific obsolete installed connection and proposed successor.
- Installation and OAuth grant identifiers, effective scopes and authenticated
  binding of each returned package/App to those instances.
- A maintained Git repository for the connector package source itself.
  UIF owns this reconciliation; that does not prove its repository owns the
  connector implementation.
- Complete external/dynamic consumer disposition and tested reversible cutover.

Supported canonical dependency lookup was tried using the exact private package
IDs. It returned `plugin_not_found` because that surface requires a public global
listed plugin. Directory search returned no matching Foo/Windmill plugin.
Neither result disproves the owned private package evidence or proves uninstall.
No alternate ID spelling, secret-store inspection or access change was used.

## Disposition

Retain both demonstrated routes, the existing workspace, scripts and historical
receipts. Preserve the existing conditional migration/rollback plan. Obtain the
missing non-secret installed identities through a supported authorized management
surface before selecting a retirement target. Current package names or a root
smoke result cannot select one.

The four existing UIF-211 action records remain partial: stable identity,
consumer mapping, successor continuity and retirement/rollback planning.
This documentation does not add a new runtime schema, controller, gate,
configuration or permission, and does not complete those actions.
