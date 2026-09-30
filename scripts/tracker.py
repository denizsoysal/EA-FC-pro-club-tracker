#!/usr/bin/env python3
"""EA Clubs -> Git-friendly archive -> static viewer. Python 3.10+, stdlib only.

No login, cookies, tokens, proxies or challenge-solving. Endpoints/event semantics
are unofficial. Validate FC27 club identity and event counters before relying on
these records. Configuration's edition is an archive label, not an API selector.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from archive_store import (ArchiveStore, ArchiveError, atomic_bytes, encoded, digest,
                           repository_lock, create_backup, verify_backup)

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://proclubs.ea.com/api/fc"
BUCKETS = tuple(f"match_event_aggregate_{i}" for i in range(4))
MATCH_TYPES = ("leagueMatch", "playoffMatch", "friendlyMatch")
# Community findings, not an EA schema. See docs/STAT_DEFINITIONS.md for sources.
# Keep raw event buckets in the archive even for counters not shown in the viewer.
EVENTS = {
    "assists_event": 11,
    "second_assists": 115,
    "dribbles_completed": 174,
    "dribble_beats_including_skill": 112,
    "skill_move_beats": 38,
    "forward_passes_completed": 30,
    "forward_passes_failed": 31,
    "long_passes_completed": 28,
    "long_passes_failed": 29,
    "through_balls_completed": 152,
    "interceptions": 6,
    "standing_tackles_won": 229,
    "sliding_tackles_won": 230,
    "opponents_dispossessed": 158,
    "shots_on_target": 217,
}
DECODER = "community-selected-stats-v2-unofficial"
VIEWER_SCHEMA = 2
CSV_STATS = [
    "goals", "assists", "second_assists", "rating", "shots", "shots_on_target",
    "passes_made", "pass_attempts", "pass_rate",
    "through_balls_completed", "dribbles_completed", "dribble_beats", "skill_move_beats",
    "interceptions", "tackles_won", "standing_tackles_won", "sliding_tackles_won",
    "opponents_dispossessed",
]
MAX_RESPONSE = 15 * 1024 * 1024
CLUB_KEYS = ("club_id", "club_name", "platform", "edition")
SAFE_LABEL = r"[a-zA-Z0-9_-]+"
# Seconds between any two API requests in one run, including across clubs.
REQUEST_PAUSE = 2


class EAError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None, retry_after: str = "",
                 unreachable: bool = False):
        super().__init__(message)
        self.code, self.retry_after, self.unreachable = code, retry_after, unreachable


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp(value: datetime | None = None) -> str:
    return (value or utcnow()).isoformat(timespec="seconds")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> bool:
    """Durable atomic replacement; preserves a previous file on a failed write."""
    return atomic_bytes(path, encoded(data))


def load_config(root: Path = ROOT) -> dict[str, Any]:
    config = read_json(root / "config.json")
    if not isinstance(config, dict):
        raise ValueError("config.json must contain a JSON object.")
    listed = "clubs" in config
    for key in ("edition", "platform"):
        # Beside a clubs list these are only defaults for entries that omit them.
        if listed and key not in config:
            continue
        value = config.get(key)
        if not isinstance(value, str) or not re.fullmatch(SAFE_LABEL, value):
            raise ValueError(f"Invalid {key}: use only letters, numbers, underscores and hyphens.")
    club = str(config.get("club_id", ""))
    if club and not re.fullmatch(r"[0-9]+", club):
        raise ValueError("club_id must be the numeric EA club ID, or blank during setup.")
    if not listed or "club_id" in config:
        config["club_id"] = club
    types = config.get("match_types", [])
    if not isinstance(types, list) or not types or len(types) != len(set(types)):
        raise ValueError("match_types must be a nonempty list without duplicates.")
    if any(t not in MATCH_TYPES for t in types):
        raise ValueError(f"Allowed match types: {MATCH_TYPES}")
    for key in ("collection_enabled", "advanced_mapping_confirmed"):
        if not isinstance(config.get(key), bool):
            raise ValueError(f"{key} must be true or false, not a string.")
    # Optional for backward compatibility. A v1 approval must not silently
    # approve the newly added community mappings.
    if not isinstance(config.get("advanced_mapping_version", ""), str):
        raise ValueError("advanced_mapping_version must be a string when supplied.")
    default_club(config)  # Validates every club and the default before anything runs.
    return config


def configured_clubs(config: dict) -> list[dict]:
    """One settings dict per club, in collection order.

    A `clubs` list is authoritative: legacy top-level club fields beside it are
    ignored, so a club named in both places is never collected twice. Without
    the list, the top-level club is the only one (none while club_id is blank).
    Every returned dict has the shape of a single-club config.
    """
    shared = {k: v for k, v in config.items() if k not in (*CLUB_KEYS, "clubs", "default_club_id")}
    if "clubs" in config:
        entries = config["clubs"]
        if not isinstance(entries, list) or not entries:
            raise ValueError("clubs must be a nonempty list of club objects.")
    else:
        entries = [config] if str(config.get("club_id", "")) else []
    clubs, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Each entry in clubs must be an object.")
        club_id = entry.get("club_id")
        if isinstance(club_id, bool) or not re.fullmatch(r"[0-9]+", str(club_id)):
            raise ValueError("Every club needs club_id: the numeric EA club ID.")
        club_id = str(club_id)
        # The ID is the viewer's selector key and dataset folder, so it must be
        # unique on its own, not only in combination with platform and edition.
        if int(club_id) in seen:
            raise ValueError(f"Duplicate club_id {club_id}: list each club once.")
        seen.add(int(club_id))
        club = {**shared, "club_id": club_id}
        for key in ("edition", "platform"):
            value = entry.get(key, config.get(key))
            if not isinstance(value, str) or not re.fullmatch(SAFE_LABEL, value):
                raise ValueError(f"Invalid {key} for club {club_id}: use only letters, "
                                 "numbers, underscores and hyphens.")
            club[key] = value
        name = entry.get("club_name", "")
        if not isinstance(name, str):
            raise ValueError(f"club_name for club {club_id} must be text.")
        club["club_name"] = name.strip() or f"Club {club_id}"
        clubs.append(club)
    return clubs


def default_club(config: dict) -> dict:
    """The club the viewer opens on; a blank placeholder while none is configured."""
    clubs = configured_clubs(config)
    wanted = config.get("default_club_id")
    if wanted is None or wanted == "":
        if clubs:
            return clubs[0]
        blank = {k: v for k, v in config.items() if k not in ("clubs", "default_club_id")}
        return {**blank, "club_id": "", "club_name": str(config.get("club_name") or "")}
    if not isinstance(wanted, bool) and re.fullmatch(r"[0-9]+", str(wanted)):
        for club in clubs:
            if int(club["club_id"]) == int(str(wanted)):
                return club
    raise ValueError("default_club_id must be the club_id of a configured club.")


def find_club(config: dict, club_id: str | None) -> dict:
    """A configured club for commands that act on one archive."""
    clubs = configured_clubs(config)
    if not clubs:
        raise ValueError("Set club_id before importing.")
    if club_id is None:
        return default_club(config)
    for club in clubs:
        if club["club_id"] == club_id:
            return club
    raise ValueError(f"Club {club_id} is not configured in config.json.")


def club_identity(club: dict) -> dict:
    """The only club settings the published viewer receives."""
    return {key: club[key] for key in CLUB_KEYS}


def club_label(club: dict) -> str:
    return f"{club.get('club_name') or 'Club'} ({club['club_id']})"


def mapping_reviewed(config: dict) -> bool:
    return (config.get("advanced_mapping_confirmed") is True
            and config.get("advanced_mapping_version") == DECODER)


def archive_dir(root: Path, config: dict) -> Path:
    return root / "data" / config["edition"] / config["platform"] / (config["club_id"] or "unconfigured")


def get_json(endpoint: str, params: dict, *, raw_sink: Callable | None = None) -> Any:
    """One normal read-only request; a denial is not retried in this function."""
    request = Request(BASE_URL + "/" + endpoint + "?" + urlencode(params), headers={
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        # Request metadata follows the community client. It is not authentication
        # and does not guarantee EA will accept a hosted runner's request.
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/141.0.0.0 Safari/537.36"),
        "Sec-Fetch-Site": "same-origin",
    })
    def preserve(code: int, body: bytes, content_type: str):
        if raw_sink:
            try:
                raw_sink(code, body[:MAX_RESPONSE], content_type, len(body) > MAX_RESPONSE)
            except OSError as exc:
                raise ArchiveError(f"Could not save the received HTTP response to disk: {exc}") from exc
    try:
        with urlopen(request, timeout=25) as response:
            body = response.read(MAX_RESPONSE + 1)
            status, content_type = response.status, response.headers.get("Content-Type", "")
    except HTTPError as exc:
        preserve(exc.code, exc.read(MAX_RESPONSE + 1), exc.headers.get("Content-Type", ""))
        raise EAError(f"EA returned HTTP {exc.code}.", code=exc.code,
                      retry_after=exc.headers.get("Retry-After", "")) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise EAError(f"EA could not be reached: {exc}", unreachable=True) from exc
    preserve(status, body, content_type)
    if len(body) > MAX_RESPONSE:
        raise EAError("EA response exceeded the safety size limit; truncated raw capture marked explicitly, no data imported.")
    try:
        return json.loads(body.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise EAError("EA returned invalid JSON or an HTML page; no data imported.") from exc


def decode_events(player: dict) -> tuple[dict[int, int] | None, str]:
    """Conservative parser. Missing/empty/malformed buckets are NOT zero."""
    if not all(k in player and isinstance(player[k], str) for k in BUCKETS):
        return None, "unavailable:missing_or_nonstring_bucket"
    if not any(player[k].strip() for k in BUCKETS):
        return None, "unavailable:empty_buckets"
    counters: dict[int, int] = {}
    for key in BUCKETS:
        for token in player[key].split(","):
            token = token.strip()
            if not token:
                continue
            parts = token.split(":")
            if len(parts) != 2 or not all(re.fullmatch(r"[0-9]+", p.strip()) for p in parts):
                return None, "unavailable:malformed_bucket"
            event, count = map(int, parts)
            if event in counters:
                return None, "unavailable:duplicate_event_id"
            counters[event] = count
    if not counters:
        return None, "unavailable:no_counters"
    return counters, "parsed:community_mapping"


def number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        n = float(value)
        if not math.isfinite(n) or n < 0:
            return None
        return int(n) if n.is_integer() else n
    except (ValueError, TypeError):
        return None


def validate_matches(data: Any, club_id: str) -> list[dict]:
    # A null result is reported by the community client as an empty feed.
    if data is None:
        return []
    if not isinstance(data, list):
        raise ValueError("Expected an EA list of matches, not an object or error message.")
    seen = set()
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("A match is not a JSON object.")
        mid = str(item.get("matchId", ""))
        if not re.fullmatch(r"[0-9]+", mid) or mid in seen:
            raise ValueError("Missing, unsafe or duplicate match ID in one response.")
        seen.add(mid)
        clubs = item.get("clubs")
        if not isinstance(clubs, dict) or club_id not in clubs or not isinstance(clubs[club_id], dict):
            raise ValueError(f"Match {mid} does not contain the configured club; response rejected.")
        if "players" in item and not isinstance(item["players"], dict):
            raise ValueError(f"Unexpected player map in match {mid}.")
    return data


def save_matches(root: Path, config: dict, match_type: str, data: Any,
                 collected_at: str | None = None) -> dict:
    """Preserve full responses before validation and ALL versions before updates."""
    if match_type not in MATCH_TYPES:
        raise ValueError("Unknown match type.")
    store = ArchiveStore(archive_dir(root, config), config)
    store.prepare()
    # Keep the whole returned JSON (both teams, unknown fields, null, even an
    # unexpected error object). Validation only controls admission to the viewer.
    snapshot = store.snapshot(match_type, data)
    matches = validate_matches(data, config["club_id"])
    when = collected_at or stamp()
    folder = store.folder / "matches"
    changes, updates = 0, {}
    preexisting = {p.stem for p in folder.glob("*.json")
                   if match_type in read_json(p).get("match_types", [])}
    for raw in matches:
        mid = str(raw["matchId"])
        path = folder / (mid + ".json")
        old = read_json(path)
        types = sorted(set((old or {}).get("match_types", [])) | {match_type})
        if old and encoded(old.get("raw")) == encoded(raw) and old.get("match_types") == types:
            continue
        record = {
            "schema_version": 1, "edition": config["edition"],
            "platform": config["platform"], "club_id": config["club_id"],
            "match_id": mid, "match_types": types,
            "first_archived_at": old["first_archived_at"] if old else when,
            "last_changed_at": when, "raw": raw,
        }
        body = encoded(record)
        if old:
            old_body = path.read_bytes()
            updates[f"revisions/{mid}/{digest(old_body)}.json"] = old_body
        updates[f"revisions/{mid}/{digest(body)}.json"] = body
        updates[f"matches/{mid}.json"] = body
        changes += 1
    store.write_many(updates)
    possible_gap = len(matches) >= 10 and bool(preexisting) and not any(
        str(m["matchId"]) in preexisting for m in matches)
    return {"received": len(matches), "changed": changes, "possible_gap": possible_gap,
            "response_was_null": data is None, "snapshot": snapshot}


def cooldown_time(header: str, now: datetime) -> str:
    try:
        seconds = int(header)
        return stamp(now + timedelta(seconds=max(seconds, 60)))
    except (ValueError, TypeError):
        try:
            dt = parsedate_to_datetime(header)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return stamp(max(dt, now + timedelta(minutes=1)))
        except (ValueError, TypeError, OverflowError):
            return stamp(now + timedelta(hours=6))


def state_files(root: Path) -> list[Path]:
    """The host-wide state file, then any written inside a club folder by single-club versions."""
    return [root / "data" / "collector_state.json",
            *sorted((root / "data").glob("*/*/*/collector_state.json"))]


def access_state(root: Path) -> dict:
    """EA's access denials and rate limits apply to this host, never to one club.

    Every state file therefore counts for every club: switching clubs must not
    become a way around a pause or a cooldown.
    """
    merged = {"halted": False, "reason": None, "cooldown_until": None}
    for path in state_files(root):
        state = read_json(path, {}) or {}
        if state.get("halted"):
            merged.update(halted=True, reason=merged["reason"] or state.get("reason"))
        until = state.get("cooldown_until")
        if until and (not merged["cooldown_until"] or datetime.fromisoformat(until)
                      > datetime.fromisoformat(merged["cooldown_until"])):
            merged["cooldown_until"] = until
    return merged


def update_states(root: Path, wanted: Callable[[dict], bool], **fields) -> None:
    for path in state_files(root):
        state = read_json(path)
        if state and wanted(state):
            state.update(fields)
            write_json(path, state)


def run_report(now: datetime, reports: dict[str, dict]) -> dict:
    """Whole-run summary. `clubs` keeps each club's own status, counts and errors."""
    rows = list(reports.values())
    statuses = {r["status"] for r in rows}
    if len(statuses) == 1:
        status = statuses.pop()
    else:
        status = "partial" if any(r["results"] for r in rows) else "error"
    if len(rows) == 1:
        message, error = rows[0]["message"], rows[0]["error"]
    else:
        messages = {r["message"] for r in rows}
        message = messages.pop() if len(messages) == 1 else " ".join(
            f"{club_label(r)}: {r['message']}" for r in rows)
        error = "; ".join(f"{club_label(r)}: {r['error']}" for r in rows if r["error"]) or None
    return {"run_at": stamp(now), "status": status, "error": error, "message": message,
            "last_api_attempt_at": max((r["last_api_attempt_at"] for r in rows
                                        if r["last_api_attempt_at"]), default=None),
            "needs_attention": any(r["needs_attention"] for r in rows), "clubs": reports}


def status_lines(report: dict) -> list[str]:
    """One line per club and match feed: records received, records changed, errors."""
    lines = []
    for club in report.get("clubs", {}).values():
        label = club_label(club)
        for match_type, result in club["results"].items():
            lines.append(f"[{label}] {match_type}: received {result['received']}, changed {result['changed']}"
                         + (" (possible gap)" if result.get("possible_gap") else ""))
        if club["error"]:
            lines.append(f"[{label}] {club.get('failed_match_type') or 'archive'}: ERROR {club['error']}")
        elif not club["results"]:
            lines.append(f"[{label}] {club['status']}: {club['message']}")
    return lines


def collect(root: Path, config: dict, *, event: str = "manual", resume: bool = False,
            fetch: Callable = get_json, pause: Callable = time.sleep) -> dict:
    """One run over every configured club. A club's failure stays with that club."""
    now = utcnow()
    clubs = configured_clubs(config)
    reports = {club["club_id"]: {
        "club_id": club["club_id"], "club_name": club["club_name"], "run_at": stamp(now),
        "last_api_attempt_at": None, "status": "not_requested", "error": None, "results": {},
        "needs_attention": False, "message": "Viewer rebuilt without an API fetch."} for club in clubs}

    def everyone(**fields) -> dict:
        for report in reports.values():
            report.update(fields)
        return run_report(now, reports)

    if event == "push":
        return run_report(now, reports)
    if not clubs:
        return {"run_at": stamp(now), "status": "setup_required", "error": None,
                "message": "Set your numeric club_id in config.json.", "last_api_attempt_at": None,
                "needs_attention": event == "manual", "clubs": {}}
    if event == "schedule" and not config["collection_enabled"]:
        return everyone(status="disabled", message="Scheduled collection is disabled in config.json.")
    if resume and event == "manual":
        # Resume clears a persistent access-denial pause, but NEVER ignores Retry-After.
        update_states(root, lambda state: state.get("halted"), halted=False, reason=None)
    state = access_state(root)
    if state["halted"]:
        return everyone(status="blocked", needs_attention=True,
                        message="Collection paused after access denial. Resolve it before a manual resume.")
    if state["cooldown_until"] and datetime.fromisoformat(state["cooldown_until"]) > now:
        return everyone(status="cooldown", needs_attention=True,
                        message="Rate-limit cooldown until " + state["cooldown_until"])
    host_path = state_files(root)[0]
    requests, fetched, denied = 0, False, False
    # Set once EA denies, rate-limits or cannot be reached: the remaining clubs
    # are not requested in this run.
    stop = None
    for club in clubs:
        report = reports[club["club_id"]]
        if stop:
            report.update(stop, needs_attention=True)
            continue
        store = ArchiveStore(archive_dir(root, club), club)
        try:
            store.prepare()
        except (ArchiveError, OSError) as exc:
            report.update(status="error", needs_attention=True, error=str(exc),
                          message="Archive integrity check failed. No API request made; no existing history overwritten.")
            continue
        report.update(status="success", message="Configured match feeds fetched successfully.")
        for match_type in club["match_types"]:
            if requests:
                pause(REQUEST_PAUSE)
            requests += 1
            report["last_api_attempt_at"] = stamp()
            try:
                params = {"platform": club["platform"], "clubIds": club["club_id"],
                          "matchType": match_type, "maxResultCount": 10}
                # Preserve successful/error HTTP response bytes before JSON decoding.
                # Injected test clients use snapshots after their decoded return.
                if fetch is get_json:
                    payload = fetch("clubs/matches", params, raw_sink=lambda code, body, ct, truncated:
                        store.http_response(match_type, code, body, ct, truncated))
                else:
                    payload = fetch("clubs/matches", params)
                report["results"][match_type] = save_matches(root, club, match_type, payload)
                fetched = True
            except (EAError, ValueError, OSError) as exc:
                report.update(status="partial" if report["results"] else "error",
                              needs_attention=True, error=str(exc), failed_match_type=match_type,
                              message="Fetch failed. Existing archived matches have been kept.")
                code = exc.code if isinstance(exc, EAError) else None
                where = f"while collecting {club_label(club)}"
                if code in (401, 403):
                    denied = True
                    host = read_json(host_path, {"halted": False, "reason": None, "cooldown_until": None})
                    host.update(halted=True, reason=f"HTTP {code}")
                    write_json(host_path, host)
                    report["message"] += " Further automatic API calls are paused."
                    stop = {"status": "blocked", "message":
                            f"Not requested: EA denied access (HTTP {code}) {where}. Collection is paused for every club."}
                elif code == 429:
                    denied = True
                    host = read_json(host_path, {"halted": False, "reason": None, "cooldown_until": None})
                    host["cooldown_until"] = cooldown_time(exc.retry_after, utcnow())
                    write_json(host_path, host)
                    report["message"] += " A persistent rate-limit cooldown is active."
                    stop = {"status": "cooldown", "message":
                            f"Not requested: EA rate-limited this host {where}. Cooldown until {host['cooldown_until']}."}
                elif isinstance(exc, EAError) and exc.unreachable:
                    stop = {"status": "skipped", "message": f"Not requested: EA could not be reached {where}."}
                break  # Do not hit this club's other endpoint after a failure/denial.
        if any(r.get("possible_gap") for r in report["results"].values()):
            report["message"] += " A full feed had no overlap with the archive: possible missing matches."
            report["needs_attention"] = True
    if fetched and not denied:
        # EA answered normally, so an expired cooldown or resumed pause is over.
        update_states(root, lambda state: state.get("cooldown_until") or state.get("reason"),
                      halted=False, reason=None, cooldown_until=None)
    return run_report(now, reports)


def completion_rate(completed: Any, attempted: Any) -> float | None:
    """A real zero completion rate differs from no attempts / missing counts."""
    made, attempts = number(completed), number(attempted)
    if made is None or attempts is None or attempts <= 0 or made > attempts:
        return None
    return 100.0 * made / attempts


def normalize_player(pid: str, player: dict) -> dict:
    events, status = decode_events(player)
    row = {"id": pid, "name": str(player.get("playername") or player.get("name") or
                                    player.get("proName") or pid),
           "position": str(player.get("pos", "")), "events_status": status,
           "data_warnings": []}
    aliases = {"goals": ("goals",), "assists": ("assists",), "rating": ("rating",),
               "shots": ("shots",), "passes_made": ("passesmade", "passesMade"),
               "pass_attempts": ("passattempts", "passAttempts"),
               "tackles_made": ("tacklesmade", "tacklesMade"), "saves": ("saves",)}
    for field, names in aliases.items():
        # An invalid first alias must not hide a valid second one.
        row[field] = next((v for n in names if n in player
                           if (v := number(player[n])) is not None), None)
    for field, event_id in EVENTS.items():
        # Sparse-zero interpretation is provisional; missing/invalid buckets
        # invalidate ALL event-derived counts, not ordinary API statistics.
        row[field] = None if events is None else events.get(event_id, 0)

    row["dribble_beats"] = None
    row["tackles_won"] = None
    for prefix in ("forward", "long"):
        row[f"{prefix}_pass_attempts"] = None
    if events is not None:
        # Source distinguishes non-skill beats (112 - 38) from skill beats (38).
        # These count actions, NOT unique defenders. Never clamp bad data to 0.
        beats = row["dribble_beats_including_skill"] - row["skill_move_beats"]
        if beats < 0:
            row["data_warnings"].append("dribble_total_below_skill_beats")
        else:
            row["dribble_beats"] = beats
        row["tackles_won"] = row["standing_tackles_won"] + row["sliding_tackles_won"]
        for prefix in ("forward", "long"):
            row[f"{prefix}_pass_attempts"] = (row[f"{prefix}_passes_completed"]
                                               + row[f"{prefix}_passes_failed"])

    row["pass_rate"] = completion_rate(row["passes_made"], row["pass_attempts"])
    for prefix in ("forward", "long"):
        row[f"{prefix}_pass_rate"] = completion_rate(
            row[f"{prefix}_passes_completed"], row[f"{prefix}_pass_attempts"])
    if (row["passes_made"] is not None and row["pass_attempts"] is not None
            and row["passes_made"] > row["pass_attempts"]):
        row["data_warnings"].append("completed_passes_exceed_attempts")
    row["assists_counter_disagrees"] = (
        row["assists"] is not None and row["assists_event"] is not None
        and row["assists"] != row["assists_event"])
    if row["assists_counter_disagrees"]:
        row["data_warnings"].append("assists_counter_disagrees")
    return row


def club_name(club: dict, fallback: str) -> str:
    details = club.get("details")
    return str(details.get("name") or fallback) if isinstance(details, dict) else fallback


def normalize_match(record: dict) -> dict:
    raw, cid = record["raw"], record["club_id"]
    clubs = raw.get("clubs", {})
    us = clubs.get(cid, {})
    opponents = [club for key, club in clubs.items() if key != cid and isinstance(club, dict)]
    them = opponents[0] if len(opponents) == 1 else {}
    goals = number(us.get("goals"))
    conceded = number(us.get("goalsAgainst"))
    if conceded is None:
        conceded = number(them.get("goals"))
    result, result_source = None, None
    flags = [(key, name) for key, name in (("wins", "W"), ("ties", "D"), ("losses", "L"))
             if number(us.get(key)) == 1]
    if len(flags) == 1:
        result, result_source = flags[0][1], "EA win/tie/loss flag"
    elif goals is not None and conceded is not None:
        result = "W" if goals > conceded else "L" if goals < conceded else "D"
        result_source = "score comparison; shoot-outs not inferred"
    players = raw.get("players", {}).get(cid, {})
    if not isinstance(players, dict):
        players = {}
    ts = number(raw.get("timestamp"))
    try:
        date = stamp(datetime.fromtimestamp(ts, timezone.utc)) if ts is not None else None
    except (ValueError, OSError, OverflowError):
        date = None
    return {"id": record["match_id"], "timestamp": date,
            "match_types": record["match_types"], "opponent": club_name(them, "Unknown opponent"),
            "goals": goals, "goals_against": conceded, "result": result,
            "result_source": result_source,
            "players": [normalize_player(str(pid), p) for pid, p in players.items() if isinstance(p, dict)],
            "first_archived_at": record["first_archived_at"],
            "last_changed_at": record["last_changed_at"]}


def dataset_dir(root: Path, club: dict) -> Path:
    # The club ID becomes a folder and URL segment of the published site.
    if not re.fullmatch(r"[0-9]+", str(club["club_id"])):
        raise ValueError("A viewer dataset needs a numeric club_id.")
    return root / "site" / "data" / "clubs" / club["club_id"]


def build(root: Path, config: dict, report: dict | None = None) -> dict:
    """One club's viewer dataset, read only from that club's archive folder."""
    store = ArchiveStore(archive_dir(root, config), config)
    if store.inventory.exists() or store.journal.exists():
        store.verify()
    records = []
    for path in sorted((archive_dir(root, config) / "matches").glob("*.json")):
        rec = read_json(path)
        if (rec.get("edition"), rec.get("platform"), rec.get("club_id")) != (
            config["edition"], config["platform"], config["club_id"]):
            raise ValueError(f"Archive identity mismatch in {path.name}.")
        records.append(normalize_match(rec))
    records.sort(key=lambda r: (r["timestamp"] or "", r["id"]), reverse=True)
    # Only the club's public identity is published, never the rest of config.json.
    payload = {"schema_version": VIEWER_SCHEMA, "club": club_identity(config), "decoder": DECODER,
               "mapping_reviewed": mapping_reviewed(config),
               "generated_at": stamp(), "collection": report or {"status": "archive_only"},
               "persistent_state": access_state(root), "repository": os.environ.get("GITHUB_REPOSITORY", ""),
               "archive_updated_at": max((m["last_changed_at"] for m in records), default=None),
               "matches": records}
    write_json(dataset_dir(root, config) / "index.json", payload)
    return payload


def build_site(root: Path, config: dict, report: dict | None = None) -> dict[str, dict]:
    """Every club's dataset and CSV, plus the catalog that fills the club selector."""
    reports = (report or {}).get("clubs", {})
    payloads, errors = {}, []
    for club in configured_clubs(config):
        try:
            payload = build(root, club, reports.get(club["club_id"]))
            export_csv(root, payload)
            payloads[club["club_id"]] = payload
        except (ValueError, OSError) as exc:
            errors.append(f"{club_label(club)}: {exc}")
    write_json(root / "site/data/clubs.json", {
        "schema_version": 1, "default_club_id": default_club(config)["club_id"],
        "generated_at": stamp(), "clubs": [club_identity(club) for club in configured_clubs(config)]})
    # Single-club versions generated these two files. A leftover copy would keep
    # serving one club's numbers outside the selector.
    for legacy in ("index.json", "player_matches.csv"):
        (root / "site/data" / legacy).unlink(missing_ok=True)
    if errors:
        raise ValueError("Viewer data could not be built. " + "; ".join(errors))
    return payloads


def export_csv(root: Path, payload: dict) -> Path:
    club = payload["club"]
    path = dataset_dir(root, club) / f"player_matches_{club['club_id']}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["club_id", "club_name", "match_id", "timestamp", "match_types", "player_id", "player_name",
              "position", *CSV_STATS, "events_status", "data_warnings", "decoder", "mapping_reviewed"]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for match in payload["matches"]:
            for p in match["players"]:
                row = {"club_id": club["club_id"], "club_name": club["club_name"],
                       "match_id": match["id"], "timestamp": match["timestamp"],
                       "match_types": ",".join(match["match_types"]),
                       "player_id": p["id"], "player_name": p["name"]}
                row.update({key: p[key] for key in fields if key in p})
                row["data_warnings"] = ";".join(p.get("data_warnings", []))
                row["decoder"] = payload.get("decoder", DECODER)
                row["mapping_reviewed"] = payload.get("mapping_reviewed", False)
                for key, value in row.items():
                    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                        row[key] = "'" + value
                writer.writerow(row)
    return path


def migrate_sqlite(root: Path, config: dict, path: Path) -> int:
    if not path.is_file():
        raise ValueError("SQLite file does not exist.")
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rows = db.execute("SELECT match_type,raw_json FROM matches WHERE edition=? AND platform=? AND club_id=?",
                          (config["edition"], config["platform"], config["club_id"])).fetchall()
    finally:
        db.close()
    total = 0
    for match_type, raw in rows:
        if match_type not in MATCH_TYPES:
            raise ValueError("Unexpected match type in SQLite archive.")
        total += save_matches(root, config, match_type, [json.loads(raw)])["changed"]
    return total


def stores_for_root(root: Path, config: dict) -> list[ArchiveStore]:
    """Every configured club plus any other archive already on disk."""
    folders = {archive_dir(root, club) for club in configured_clubs(config)}
    for pattern in ("*/*/*/matches", "*/*/*/integrity.json"):
        for path in (root / "data").glob(pattern):
            folders.add(path.parent)
    stores = []
    for folder in sorted(folders):
        edition, platform, club_id = folder.relative_to(root / "data").parts
        stores.append(ArchiveStore(folder, {"edition": edition, "platform": platform, "club_id": club_id}))
    return stores


def archive_report(root: Path, config: dict, *, protect: bool = False) -> dict:
    """Check each club archive on its own, so one failure never hides another club's result."""
    names = {archive_dir(root, club): club["club_name"] for club in configured_clubs(config)}
    stores = stores_for_root(root, config)
    if not stores:
        raise ArchiveError("No configured club or archive to check.")
    archives = []
    for store in stores:
        entry = {**store.identity, "club_name": names.get(store.folder),
                 "path": store.folder.relative_to(root).as_posix()}
        try:
            entry.update(store.prepare() if protect else store.verify())
        except (ValueError, OSError, KeyError, TypeError) as exc:
            entry.update(status="error", error=str(exc))
        archives.append(entry)
    healthy = all(entry["status"] == "ok" for entry in archives)
    return {"status": "ok" if healthy else "error", "archives": archives}


def archive_failures(report: dict) -> str:
    return "; ".join(f"club {a['club_id']} ({a['path']}): {a['error']}"
                     for a in report["archives"] if a["status"] != "ok")


def archive_check(root: Path, config: dict, *, protect: bool = False) -> dict:
    """archive_report, but any unhealthy archive is an error. `.report` has every club."""
    report = archive_report(root, config, protect=protect)
    if report["status"] != "ok":
        error = ArchiveError("Archive check failed for " + archive_failures(report))
        error.report = report
        raise error
    return report


def run_main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    search = sub.add_parser("search", help="Read the club search API once; print results")
    search.add_argument("name")
    sync = sub.add_parser("sync", help="Fetch, archive and build; reports failures after saving good data")
    sync.add_argument("--event", choices=("manual", "schedule", "push"), default="manual")
    sync.add_argument("--resume", action="store_true", help="Only after an access denial is resolved")
    sync.add_argument("--defer-failure", action="store_true", help="Workflow: let Git commit and Pages deploy first")
    sub.add_parser("build", help="Rebuild viewer data without calling EA")
    sub.add_parser("check-status", help="Fail when the last sync report needs attention")
    sub.add_parser("verify", help="Check JSON, identities, SHA-256 hashes and missing files; recover a pending transaction")
    backup = sub.add_parser("backup", help="Protect existing history and create a SHA-256-verified ZIP of ALL data and config")
    backup.add_argument("--output", type=Path, help="New ZIP filename, ideally on another disk")
    vb = sub.add_parser("verify-backup", help="Check every file in a backup ZIP")
    vb.add_argument("file", type=Path)
    imp = sub.add_parser("import-json", help="Import an EA match-list JSON obtained legitimately")
    imp.add_argument("file", type=Path)
    imp.add_argument("--match-type", choices=MATCH_TYPES, default="leagueMatch")
    mig = sub.add_parser("import-sqlite", help="Migrate the previous fc_clubs.sqlite3 archive")
    mig.add_argument("file", type=Path)
    for command in (imp, mig):
        command.add_argument("--club", metavar="CLUB_ID", help="Configured club to import into (default: default club)")
    args = parser.parse_args()
    try:
        config = load_config()
        report_path = ROOT / ".runtime/report.json"
        if args.command == "search":
            print(json.dumps(get_json("allTimeLeaderboard/search", {"platform": default_club(config)["platform"],
                            "clubName": args.name}), indent=2, ensure_ascii=False))
            return 0
        if args.command == "check-status":
            return int(bool(read_json(report_path, {}).get("needs_attention")))
        if args.command == "verify":
            checked = archive_report(ROOT, config)
            print(json.dumps(checked, indent=2))
            if checked["status"] != "ok":
                print("Error: Archive check failed for " + archive_failures(checked), file=sys.stderr)
                return 1
            return 0
        if args.command == "backup":
            archive_check(ROOT, config, protect=True)
            print(json.dumps(create_backup(ROOT, args.output), indent=2))
            return 0
        if args.command == "verify-backup":
            print(json.dumps(verify_backup(args.file), indent=2))
            return 0
        if args.command in ("import-json", "import-sqlite"):
            club = find_club(config, args.club)
            if args.command == "import-json":
                if not args.file.is_file():
                    raise ValueError("Input JSON file does not exist.")
                print(save_matches(ROOT, club, args.match_type, read_json(args.file)))
            else:
                print("Imported/updated:", migrate_sqlite(ROOT, club, args.file))
        report = None
        if args.command == "sync":
            # Every accepted match is already on disk when collect returns; the
            # viewer build and any failure report come afterwards.
            report = collect(ROOT, config, event=args.event, resume=args.resume)
            write_json(report_path, report)
            print(json.dumps(report, indent=2))
            print("\n".join(status_lines(report)))
        payloads = build_site(ROOT, config, report)
        for payload in payloads.values():
            print(f"Viewer contains {len(payload['matches'])} distinct archived matches "
                  f"for {club_label(payload['club'])}.")
        if not payloads:
            print("Viewer contains no club yet. Set club_id in config.json.")
        if report and report["needs_attention"] and not args.defer_failure:
            return 1
        return 0
    except (ValueError, OSError, EAError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    try:
        with repository_lock(ROOT):
            return run_main()
    except (ArchiveError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
