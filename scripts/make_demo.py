#!/usr/bin/env python3
"""Build clearly labelled synthetic examples. Never writes to the raw archive."""
import importlib.util
from pathlib import Path
from datetime import datetime, timedelta, timezone

spec = importlib.util.spec_from_file_location('tracker', Path(__file__).with_name('tracker.py'))
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
root = Path(__file__).resolve().parents[1]
config = t.load_config(root)
config.update(club_id='000000', club_name='Dubs VzeDoux · Demo',
              advanced_mapping_confirmed=False, advanced_mapping_version='')
now = datetime.now(timezone.utc)
matches = []
names = ['Creator 10', 'Finisher 9', 'Anchor 6', 'Runner 7', 'Keeper 1']
opponents = ['Harbour Athletic', 'Metro Eleven', 'Forest United', 'Northside FC', 'Riverside Rovers']
for i in range(14):
    when = t.stamp(now - timedelta(days=i // 3, hours=(i % 3) * 2 + 1))
    players = []
    goals, conceded = [(4, 1), (2, 0), (1, 2), (3, 3), (5, 2), (2, 1), (1, 0)][i % 7]
    for j, name in enumerate(names):
        g = goals if j == 1 else 0
        a = min(goals, (i + j) % 3) if j in (0, 3) else 0
        skill_beats = (i + j) % 4 if j in (0, 1, 3) else 0
        non_skill_beats = 1 + (i + j) % 6 if j != 4 else 0
        forward = 8 + (2 * i + j) % 10
        forward_failed = 1 + (i + j) % 4
        passes_made = forward + 12 + (i * j) % 13
        on_target = g + ((i + j) % 3 if j != 4 else 0)
        counters = {
            11: a, 115: (i + j) % 3 if j == 0 else 0,
            174: 6 + (i + j) % 12 if j != 4 else 0,
            112: non_skill_beats + skill_beats, 38: skill_beats,
            30: forward, 31: forward_failed, 28: (i + j) % 8, 29: (i + j) % 3,
            152: 2 + i % 4 if j == 0 else (i + j) % 2,
            6: 3 + i % 4 if j == 2 else (i + j) % 3,
            229: 4 + i % 3 if j == 2 else (i + j) % 3,
            230: (i + j) % 2 if j != 4 else 0,
            158: (i + j) % 4 if j != 4 else 0,
            217: on_target,
        }
        p = {'playername': name, 'goals': str(g), 'assists': str(a),
             'rating': str(round(6.6 + ((i + 2 * j) % 16) / 10, 1)),
             'shots': str(on_target + ((i + j) % 3 if j != 4 else 0)),
             'passesmade': str(passes_made), 'passattempts': str(passes_made + forward_failed + 3)}
        items = list(counters.items())
        for bucket, key in enumerate(t.BUCKETS):
            p[key] = ','.join(f'{event}:{count}' for event, count in items[bucket::4])
        # Demonstrate unavailable data, not a row of false zeros.
        if i in (4, 9):
            for key in t.BUCKETS:
                p[key] = ''
        players.append(t.normalize_player(str(1000 + j), p))
    kind = 'friendlyMatch' if i in (4, 9) else 'playoffMatch' if i % 5 == 0 else 'leagueMatch'
    matches.append({'id': str(900000 + i), 'timestamp': when, 'match_types': [kind],
                    'opponent': opponents[i % len(opponents)], 'goals': goals, 'goals_against': conceded,
                    'result': 'W' if goals > conceded else 'D' if goals == conceded else 'L',
                    'result_source': 'synthetic demonstration', 'players': players,
                    'first_archived_at': when, 'last_changed_at': when})
payload = {'schema_version': t.VIEWER_SCHEMA, 'config': config, 'decoder': t.DECODER,
           'mapping_reviewed': False, 'generated_at': t.stamp(now),
           'collection': {'status': 'demo', 'message': 'Synthetic demonstration only. No EA data or real collection status.',
                          'run_at': t.stamp(now), 'last_api_attempt_at': None},
           'persistent_state': {}, 'repository': '', 'archive_updated_at': None, 'matches': matches}
t.write_json(root / 'site/demo.json', payload)
print('Updated site/demo.json (synthetic only; no archive writes).')
