# Collector staging checkpoint

Source baseline: `149c450e378c5121ddcf725fe4c9bcc537ec1aec`, draft UIF PR #21.
Read-only host observation and artifact staging: 9 November 2026.

## What is staged

All 57 monitor files at the baseline were materialized on Foo and verified against
their Git blob hashes. A concrete collector unit was rendered into a separate
private artifact directory, outside systemd's unit load paths. It points at that
pinned source, the existing collector configuration, and the existing approved
allowance credential file. Only the credential file's path is referenced; its
contents were not opened or copied.

The unit was **not installed, enabled, or started**. No daemon reload, service
restart, source observation, network delivery, security change, or new credential
was performed. No Morgan unit file or enablement link was found in the checked
system and user unit directories. The system bus was unavailable to this command
environment, so this is not an exhaustive live process inventory.

The staged unit's SHA-256 is
`0a13659e4e3252cbf050fe93a6c1c006edee033fcf275e6be4ffe9dd52ab398d`.
Its exact local paths and dated readiness record remain in the private staging
artifact, not the public dashboard or repository configuration. Rebuilding with
different approved paths changes this artifact hash.

`tools/morgan_monitor/staging.py` is a text renderer only. It accepts literal
absolute paths from a restricted character set, rejects systemd substitutions
and line injection, and reads only the checked-in service template. It never
opens the referenced configuration or credential file, saves a unit, invokes
systemd, starts a process, or performs network I/O. The caller must retain its
output outside unit load paths until deployment is separately authorized.

## Verified readiness and limits

- Existing private configuration passes `check-config` without source reads or
  networking. It selects only `foo_allowance`, with a 30-second poll interval,
  five-second allowance-read timeout, five-second delivery timeout and no session
  bindings. All registry source paths are unset.
- The configured allowance socket exists, is mode 0600 and matches its pinned
  owner. The executable exists and its SHA-256 still matches the pin. A successful
  current allowance read has **not** been established by these metadata checks.
- The dedicated collector state database is absent, so no existing collector
  queue or sequence was initialized, overwritten or repurposed by staging.
- Read-only Cloudflare management checks found only allowance and reviewed-topic
  source registrations, no aliases, zero observations and zero source heads.
  The minute cron is configured. One unchanged retained snapshot does not prove
  that scheduled execution is currently succeeding.
- Browser source polls every five seconds while visible, 30 seconds while
  hidden, with bounded failure backoff. Collector polling begins after the prior
  pass completes, so read and delivery time add latency. These are configured
  polling cadences, not event-driven or measured end-to-end freshness.

## Remaining activation work

1. Respect the existing design/data boundary before populating the design.
2. Resolve the previously denied machine-ingress security exception through its
   approved human/security path. No retry, alternate route or bypass is included.
3. Qualify a successful read from the pinned allowance source, then install and
   activate the supervised collector using the already approved source identity
   and credential scope. Staging is not permission to create or expand access.
4. Verify original-route ingestion, exact acknowledgments, restart/retry custody,
   outside-source expiry and the actual visible refresh delay.
5. Worker/session cells additionally require qualified source semantics, explicit
   identity-to-alias mappings and their scoped receiver registration. Allowance
   and historical metadata cannot stand in for worker execution evidence.

## Validation

- `systemd-analyze verify` on the concrete staged unit: exit 0, no diagnostics.
  This parses the unit; it does not install or run the service.
- Four synthetic staging tests cover literal path rendering, no activation
  commands, no reads/copies of configuration or credentials, injection rejection
  and template-drift failure.
- All 100 runnable Python tests and all 98 Node tests pass on the staged source;
  service syntax and whitespace checks pass. Eleven existing allowance Unix-socket
  tests remain explicitly excluded for the previously recorded environment limit.
  No full Python-suite or production-service pass is claimed. Independent scoped
  review found no actionable issue in the renderer or artifact.
- The staging helper does not change the collector, receiver, frontend, source
  allowlists or deployment settings. Existing live acceptance gaps remain open.
