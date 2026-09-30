# Persistent raw history

This complete source package contains no real match data. Supply config.json,
then run `python scripts/tracker.py sync` to collect available matches.

The collector creates EDITION/PLATFORM/CLUB_ID/ for each configured club. Every
club folder is independent and has these protected groups:

- matches/: latest complete accepted match records, all original fields kept;
- revisions/: retained versions of changed match records;
- snapshots/: complete decoded endpoint responses;
- responses/: captured HTTP response bodies in JSON envelopes;
- integrity.json: SHA-256 inventory used to detect missing or altered files.

Empty API responses never delete saved records. Duplicate data is deduplicated.
A sync creates the checksum baseline for a fresh archive. `verify` does not
fetch data. Keep this entire directory together, not just matches/.

collector_state.json, directly in this directory, preserves access-denial pauses
and rate-limit cooldowns. They apply to every club, because EA denies or limits
the host, not one club. A copy inside a club folder comes from an earlier
single-club version and is still honoured. Do not delete either to repeatedly
retry a rejected request.

Commit ALL of data/ to your repository. Raw data in a public repository is
public, including player identifiers, nicknames and opponent information.
Keep verified backups in independent storage. See docs/HISTORY_SAFETY.md.
