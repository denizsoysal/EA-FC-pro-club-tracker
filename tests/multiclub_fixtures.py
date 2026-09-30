"""Synthetic multi-club fixtures shared by the Python, DOM and HTTP tests.

No EA data and no live requests. Alpha and Beta play each other once (match
9001), and player 500 appears for both clubs, so any mixing of club data shows
up as a wrong number. Gamma is configured but has no matches.

Expected viewer numbers:
  Alpha United: 3 matches, record 1-1-1, goals 4:4. Striker 1 goal, Anchor 3.
  Beta Town:    2 matches, record 1-0-1, goals 5:3. Striker 3 goals, Runner 2.
"""
import copy

from test_tracker import t

ALPHA, BETA, GAMMA = '100', '200', '300'
NAMES = {ALPHA: 'Alpha United', BETA: 'Beta Town', GAMMA: 'Gamma Empty'}
START = 1790596800


def config(clubs=(ALPHA, BETA), **extra):
    return {'default_club_id': clubs[0],
            'clubs': [{'club_id': cid, 'club_name': NAMES[cid], 'platform': 'common-gen5', 'edition': 'fc27'}
                      for cid in clubs],
            'match_types': ['leagueMatch', 'playoffMatch'], 'collection_enabled': True,
            'advanced_mapping_confirmed': False, **extra}


def player(name, goals=0, dribbles=0, tackles=0):
    return {'playername': name, 'goals': str(goals), 'assists': '0', 'rating': '7.5',
            'shots': str(goals + 1), 'passesmade': '20', 'passattempts': '25',
            'match_event_aggregate_0': f'174:{dribbles}', 'match_event_aggregate_1': f'229:{tackles}',
            'match_event_aggregate_2': '', 'match_event_aggregate_3': ''}


def fixture(mid, hours, first, second):
    """Each side is (club_id, club_name, goals, {player_id: player})."""
    clubs, players = {}, {}
    for (cid, name, goals, squad), other in ((first, second), (second, first)):
        conceded = other[2]
        clubs[cid] = {'goals': str(goals), 'goalsAgainst': str(conceded), 'wins': str(int(goals > conceded)),
                      'ties': str(int(goals == conceded)), 'losses': str(int(goals < conceded)),
                      'details': {'name': name}}
        players[cid] = squad
    return {'matchId': mid, 'timestamp': str(START + hours * 3600), 'clubs': clubs, 'players': players}


DERBY = fixture('9001', 0,
                (ALPHA, NAMES[ALPHA], 3, {'500': player('Shared Striker', 1, dribbles=4),
                                          '501': player('Alpha Anchor', 2, tackles=5)}),
                (BETA, NAMES[BETA], 1, {'600': player('Beta Keeper'), '601': player('Beta Runner', 1, dribbles=2)}))
ALPHA_LOSS = fixture('9002', 1,
                     (ALPHA, NAMES[ALPHA], 0, {'500': player('Shared Striker'), '501': player('Alpha Anchor', tackles=2)}),
                     ('900', 'Opponent X', 2, {'910': player('X Forward', 2)}))
ALPHA_PLAYOFF = fixture('9003', 2,
                        (ALPHA, NAMES[ALPHA], 1, {'500': player('Shared Striker'), '501': player('Alpha Anchor', 1)}),
                        ('901', 'Opponent Y', 1, {}))
BETA_WIN = fixture('9004', 3,
                   (BETA, NAMES[BETA], 4, {'500': player('Shared Striker', 3, dribbles=6), '600': player('Beta Keeper'),
                                           '601': player('Beta Runner', 1)}),
                   ('902', 'Opponent Z', 0, {}))
FEEDS = {(ALPHA, 'leagueMatch'): [DERBY, ALPHA_LOSS], (ALPHA, 'playoffMatch'): [ALPHA_PLAYOFF],
         (BETA, 'leagueMatch'): [DERBY, BETA_WIN], (BETA, 'playoffMatch'): []}


def fetcher(feeds=None, failures=None):
    """A mocked API client. Returns (fetch, calls); calls lists (club_id, match_type)."""
    feeds, failures, calls = FEEDS if feeds is None else feeds, failures or {}, []

    def fetch(endpoint, params):
        key = (params['clubIds'], params['matchType'])
        calls.append(key)
        assert endpoint == 'clubs/matches' and ',' not in key[0], 'one club per request'
        if key in failures:
            raise failures[key]
        return copy.deepcopy(feeds.get(key, []))
    return fetch, calls


def sync(root, cfg, **options):
    """One mocked collection run over every configured club, without pauses."""
    fetch, calls = fetcher(options.pop('feeds', None), options.pop('failures', None))
    report = t.collect(root, cfg, fetch=fetch, pause=lambda _: None, **options)
    return report, calls


def publish(root, clubs=(ALPHA, BETA, GAMMA)):
    """Archive the fixtures under root and build the viewer data in root/site/data."""
    cfg = config(clubs)
    t.write_json(root / 'config.json', cfg)
    report, _ = sync(root, cfg)
    return cfg, t.build_site(root, cfg, report)
