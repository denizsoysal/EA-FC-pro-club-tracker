# Persistent raw history

This complete source package contains no real match data. Supply config.json,
then run `python scripts/tracker.py sync` to collect available matches.

The collector creates EDITION/PLATFORM/CLUB_ID/ with these protected groups:

- matches/: latest complete accepted match records, all original fields kept;
- revisions/: retained versions of changed match records;
- snapshots/: complete decoded endpoint responses;
- responses/: captured HTTP response bodies in JSON envelopes;
- integrity.json: SHA-256 inventory used to detect missing or altered files.

Empty API responses never delete saved records. Duplicate data is deduplicated.
A sync creates the checksum baseline for a fresh archive. `verify` does not
fetch data. Keep this entire directory together, not just matches/.

collector_state.json preserves access-denial pauses and rate-limit cooldowns.
Do not delete it to repeatedly retry a rejected request.

Commit ALL of data/ to your repository. Raw data in a public repository is
public, including player identifiers, nicknames and opponent information.
Keep verified backups in independent storage. See docs/HISTORY_SAFETY.md.
