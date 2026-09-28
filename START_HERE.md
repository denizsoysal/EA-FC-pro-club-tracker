# DubsFC Tracker — start here

This ZIP is the whole source project. You do not need any previous ZIP.

1. Extract the `dubsfc-tracker` folder. Keep your old backups or Git repository.
2. Add your own `config.json` inside that folder, beside `README.md` and `scripts/`.
   Set the numeric club ID to `696276` for Dubs VzeDoux. Keep all other required
   settings in your config. Use Python 3.10 or newer.
3. Open PowerShell inside that folder and run:

```powershell
python scripts/tracker.py sync
```

If sync succeeds and reports archived matches, run:

```powershell
python scripts/tracker.py backup
python scripts/tracker.py verify
python -m http.server 8000 --bind 127.0.0.1 --directory site
```

Open `http://localhost:8000`. Leave the server running while viewing; Ctrl+C
stops it. No `pip install` is needed for the collector or local viewer.

## Empty is not broken

There is deliberately no real match archive or integrity baseline in this ZIP.
`sync` initializes the archive, fetches the available matches and builds the
viewer. `backup` and `verify` do not fetch anything. An integrity check that says
`matches: 0` confirms an empty archive, not a full history. Old deleted matches
can only return if still available from the endpoint or restored from an older
backup/Git copy containing them. Do not delete `data/` after collecting.

## Configuration

Supply your existing configuration, including `club_name`, `club_id`, `platform`,
`edition`, `match_types`, `collection_enabled`, and `advanced_mapping_confirmed`.
For this club the ID is `696276` and platform is `common-gen5`; `edition` is your
local archive label. A manual sync runs even when `collection_enabled` is false.
That switch controls scheduled runs only. You do not need to change the mapping
confirmation to make the advanced columns display. Event meanings remain
community-reported unless independently checked against the game.

## GitHub

The full `.github/workflows/archive-and-publish.yml`, `.gitattributes`, and
`.gitignore` are included. Commit the complete source, your configuration and
ALL of `data/`. Configure Pages to use GitHub Actions. Run the workflow manually
first and inspect both fetch and persistence. Enable scheduled collection in your
config only after the hosted test succeeds. The workflow targets every 15 minutes;
it is not a guaranteed timer. Copy backups to independent storage.

Live EA access and a hosted deployment are not tested by extracting this ZIP.
The included tests use synthetic data; see PACKAGE_VERIFICATION.md.
