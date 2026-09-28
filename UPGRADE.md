# Using this complete package with an existing installation

For a fresh empty installation, follow START_HERE.md. No previous package is
required. This file is only for an existing project that still has match data.

Do not delete that project's `data/`, `config.json`, backups, or `.git` folder.
Make an independent copy before merging this package into the project root.
The complete package supplies all code/site/tests/docs and the hardened GitHub
workflow; it does not contain configuration or real matches. Preserve custom
schedule and ignore rules when merging. Keep the `.gitattributes` protections.

For an existing legacy archive, stop running collectors/local servers, then run:

```powershell
python scripts/tracker.py backup
python scripts/tracker.py verify
python scripts/tracker.py build
```

The first backup initializes the integrity baseline only when valid to do so.
It does not prove old files were complete. If an existing inventory reports
missing/corrupted files, stop and retain the files for recovery rather than
removing the inventory. Keep a backup on another drive.

An empty project is different: run `sync` to collect available records. A backup
of an empty history cannot restore records deleted before it was created.
