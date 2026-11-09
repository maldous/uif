# Reviewed generic status subjects

The single registered `curation` source may emit `conversationTopics.status`
when a reviewed public subject has no verified Voice/Chat provenance. The name
`conversationTopics` remains for protocol compatibility. Generic subjects are
never copied into Voice or Chat merely to fill a display region.

`conversationTopics` accepts a nonempty subset of `voice`, `chat`, and `status`.
Each channel value is an array of reviewed catalog keys, newest first. The
limits are ten keys per Voice/Chat event and twelve per generic-status event.
Unknown fields, arbitrary text/URLs, duplicate keys and unreviewed keys fail
closed. Only the existing authenticated curation capability may use the object.

An omitted channel makes no change. An empty array makes no new observation and
does not erase earlier subjects. New mentions move a key to the front, exact
event retries do not renew dates, and the oldest entries are evicted at each
channel's bound. Ties preserve that event's array order. Public subjects contain
only `{key, text, observedAt}`. The timestamp is the verified curation/receipt
time supplied for the event, not an inferred speaker utterance time.

The three approved current generic keys are:

1. `monitor_service_implementation`: Monitor service implementation
2. `compact_display`: Compact display
3. `layout_design`: Layout design

Their approved curation/receipt time is `2026-11-09T05:21:09.000Z`. An initial
publication can use source `reviewed_topics`, sequence 1 and a unique event ID
only after checking that source's current durable head. Its event fields are:

```json
{
  "schemaVersion": 1,
  "sourceId": "reviewed_topics",
  "eventId": "initial-curated-status-1",
  "sequence": 1,
  "observedAt": "2026-11-09T05:21:09.000Z",
  "health": "ok",
  "coverage": {"complete": false, "observed": 0, "total": null},
  "sessions": [],
  "conversationTopics": {
    "status": ["monitor_service_implementation", "compact_display", "layout_design"]
  }
}
```

This file documents authorized public curation; it does not itself publish the
event or claim that the source has been deployed. No Voice/Chat attribution,
active agent, quota state or execution coverage is asserted by this event.

New public snapshots include `status: []` when there are no generic subjects.
Old immutable snapshots lacking this optional property remain readable without
being rewritten, rehashed or given invented provenance.

Apply migration `0005_generic_status_topics.sql` as one atomic migration batch
after migrations 0001–0004. SQLite requires recreating the table to extend its
channel CHECK constraint. The migration retains the prior table as
`curated_topics_before_status`, copies its rows into the extended table, and
replaces only the topic trigger. It does not delete old topic data or snapshots.
The archive stops receiving new writes and remains bounded by the previous
per-source retention. Do not apply migration statements concurrently or in a
partially committed sequence while ingestion is running.

## Later reviewed subjects

At curation time `2026-11-09T05:46:37.101Z`, the user's actual current requests
added `worker_identity_mapping` (Worker identity mapping) and
`expressive_animation` (Expressive animation), in that newest-left order.
A subsequent source event should carry only those two new keys, preserving the
older observations above. Do not renew all older subjects on a rendering update.
