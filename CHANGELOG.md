# v3 — DubsFC Tracker and protected history

- Requested branding, boilerplate removal, simplified passing and no event-coverage UI.
- Clear completed/successful-dribble wording; recovery wording explicitly limited to dispossessions.
- Full response snapshots and captured HTTP bodies; all accepted match revisions retained.
- SHA-256 inventory, journaled/recoverable writes, fsync, OS writer lock, verified ZIP backups.
- Preserve raw Git bytes across Windows line endings; compare staged blob hashes before committing.
- Persist raw data after viewer failures; normal-push retries, conflict aborts and recovery artifacts.
- Added fault injection, real process-exit recovery tests, local bare-Git round trips and legacy upgrade tests.
- No automatic configuration edits, data deletions, or semantic-mapping approval.

---

# Changelog

## v2 — 28 September 2026

- Added Overview, Passing, Dribbling and Defending tabs, also available in each
  expanded match. No crossing/corner/header columns.
- Added all selected passing, dribbling, defending and shooting metrics to the
  normalizer, viewer and per-match CSV. Raw archive format is unchanged.
- Kept second assists separate from key passes. No speculative pass-bypass metric.
- Non-skill dribble beats use the reported `112 − 38` formula; skill beats use 38.
  Impossible negative differences remain unavailable with an audit warning.
- Added weighted pass-completion percentages, meaningful per-available-match
  denominators, partial-coverage indicators and metric definitions.
- Added filtered current-view CSV downloads, last-5/last-10 filters, keyboard tab
  navigation and a sticky player column with a mobile scrolling hint.
- Kept backwards compatibility with existing config files, raw matches and the
  original archive/publish workflow. Viewer schema increased to v2.
- Mapping-review approval is tied to the decoder version so prior approval of
  two advanced counters does not silently approve the additional mappings.
- Added Python, dependency-free Node and optional offline browser regression tests.
- Supplied a data/config/workflow-preserving update ZIP plus a complete starter.

No live EA access, real FC27 event mapping, or account deployment was validated.
