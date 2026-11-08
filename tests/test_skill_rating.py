"""Skill ratings of both teams, fetched after recent matches. Mocked EA only.

EA's club stats give a club's CURRENT skill rating; no history exists. These
tests check what is fetched, how often, what is kept, and how the viewer data
says whether a rating was taken right after a match or later.
"""
import base64
import copy
import csv
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from test_tracker import t
import archive_store as a
import multiclub_fixtures as f
from multiclub_fixtures import ALPHA, BETA, stats_reply


def recent(mid, minutes_ago, them='900', name='Opponent', score=(2, 1), us=ALPHA):
    """A match played `minutes_ago` minutes before now."""
    match = f.fixture(mid, 0, (us, f.NAMES[us], score[0], {'500': f.player('Shared Striker', score[0])}),
                      (them, name, score[1], {}))
    match['timestamp'] = str(int(time.time()) - minutes_ago * 60)
    return match


class SkillRatingTests(unittest.TestCase):
    clubs = (ALPHA,)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cfg = f.config(self.clubs)
        t.write_json(self.root / 'config.json', self.cfg)

    def tearDown(self):
        self.temp.cleanup()

    def sync(self, league, stats, **options):
        feeds = {(ALPHA, 'leagueMatch'): league, (ALPHA, 'playoffMatch'): options.pop('playoff', [])}
        feeds.update(options.pop('more_feeds', {}))
        return f.sync(self.root, self.cfg, feeds=feeds, stats=stats, **options)

    def stats_calls(self, calls):
        return [club for club, kind in calls if kind == 'overallStats']

    def viewer(self, club=ALPHA):
        return {m['id']: m for m in t.build_site(self.root, self.cfg)[club]['matches']}

    def folder(self, club=ALPHA):
        return t.archive_dir(self.root, t.find_club(self.cfg, club))

    def test_a_new_match_gets_both_ratings_right_after_it(self):
        stats = {ALPHA: stats_reply(ALPHA, 1808, ['900']), '900': stats_reply('900', 1758, [ALPHA])}
        report, calls = self.sync([recent('9500', 20)], stats)
        self.assertEqual(calls, [(ALPHA, 'leagueMatch'), (ALPHA, 'playoffMatch'), (ALPHA, 'overallStats'), ('900', 'overallStats')])
        self.assertEqual(report['clubs'][ALPHA]['skill_ratings'], {'fetched': 2, 'errors': []})
        self.assertFalse(report['needs_attention'])
        skill = self.viewer()['9500']['skill']
        self.assertEqual((skill['club']['rating'], skill['club']['matches_since']), (1808, 0))
        self.assertEqual((skill['opponent']['rating'], skill['opponent']['matches_since']), (1758, 0))
        self.assertIn('[Alpha United (100)] skill ratings: fetched 2', t.status_lines(report))

    def test_ratings_are_fetched_once_per_match(self):
        stats = {ALPHA: stats_reply(ALPHA, 1808, ['900']), '900': stats_reply('900', 1758, [ALPHA])}
        self.sync([recent('9500', 20)], stats)
        report, calls = self.sync([recent('9500', 20)], stats)
        self.assertEqual(self.stats_calls(calls), [])
        self.assertEqual(report['clubs'][ALPHA]['skill_ratings'], {'fetched': 0, 'errors': []})
        # A new match the next run needs this club again, and only the new opponent.
        stats['901'] = stats_reply('901', 1500, [ALPHA])
        _, calls = self.sync([recent('9501', 5, them='901'), recent('9500', 20)], stats)
        self.assertEqual(self.stats_calls(calls), [ALPHA, '901'])

    def test_older_matches_are_left_without_a_rating(self):
        stats = {ALPHA: stats_reply(ALPHA, 1808), '900': stats_reply('900', 1758)}
        old = recent('9502', 49 * 60)
        report, calls = self.sync([old], stats)
        self.assertEqual(self.stats_calls(calls), [])
        self.assertEqual(self.viewer()['9502']['skill'], {'club': None, 'opponent': None})
        self.assertNotIn('skill ratings', ' '.join(t.status_lines(report)))

    def test_rating_taken_after_more_matches_says_how_many(self):
        stats = {ALPHA: stats_reply(ALPHA, 1810, ['900']),
                 '900': stats_reply('900', 1700, ['903', '902', ALPHA, '901'])}
        self.sync([recent('9503', 25)], stats)
        skill = self.viewer()['9503']['skill']
        self.assertEqual(skill['club']['matches_since'], 0)
        self.assertEqual(skill['opponent']['matches_since'], 2)

    def test_rating_taken_too_late_has_unknown_distance(self):
        stats = {ALPHA: stats_reply(ALPHA, 1810, ['900']),
                 '900': stats_reply('900', 1700, [str(800 + i) for i in range(10)])}
        self.sync([recent('9504', 25)], stats)
        opponent = self.viewer()['9504']['skill']['opponent']
        self.assertEqual((opponent['rating'], opponent['matches_since']), (1700, None))

    def test_two_matches_against_the_same_club_are_told_apart(self):
        stats = {ALPHA: stats_reply(ALPHA, 1820, ['900', '900']), '900': stats_reply('900', 1690, [ALPHA, ALPHA])}
        report, calls = self.sync([recent('9506', 30), recent('9505', 60)], stats)
        self.assertEqual(self.stats_calls(calls), [ALPHA, '900'])  # One reply serves both matches.
        view = self.viewer()
        self.assertEqual([view[mid]['skill']['club']['matches_since'] for mid in ('9506', '9505')], [0, 1])
        self.assertEqual([view[mid]['skill']['opponent']['matches_since'] for mid in ('9506', '9505')], [0, 1])

    def test_requests_per_run_are_capped_and_the_rest_follows(self):
        league = [recent(str(9600 + n), 10 + n, them=str(700 + n)) for n in range(10)]
        playoff = [recent(str(9610 + n), 30 + n, them=str(710 + n)) for n in range(2)]
        stats = {ALPHA: stats_reply(ALPHA, 1800)} | {str(c): stats_reply(str(c), 1500 + c) for c in range(700, 712)}
        report, calls = self.sync(league, stats, playoff=playoff)
        first = self.stats_calls(calls)
        self.assertEqual(len(first), t.STATS_REQUESTS_PER_RUN)
        self.assertEqual(first[:2], [ALPHA, '700'])  # The club itself, then the newest opponents.
        self.assertTrue(report['clubs'][ALPHA]['skill_ratings']['deferred'])
        self.assertIn('[Alpha United (100)] skill ratings: fetched 10, rest next run', t.status_lines(report))
        _, calls = self.sync(league, stats, playoff=playoff)
        self.assertEqual(self.stats_calls(calls), ['709', '710', '711'])
        self.assertTrue(all(m['skill']['opponent'] for m in self.viewer().values()))

    def test_budget_is_shared_between_clubs(self):
        self.cfg = f.config((ALPHA, BETA))
        league = [recent(str(9700 + n), 10 + n, them=str(700 + n)) for n in range(10)]
        beta = [recent('9800', 15, them='880', us=BETA)]
        stats = {ALPHA: stats_reply(ALPHA, 1800), BETA: stats_reply(BETA, 1600)} | {
            str(c): stats_reply(str(c), 1500) for c in (*range(700, 710), 880)}
        _, calls = self.sync(league, stats, more_feeds={(BETA, 'leagueMatch'): beta})
        self.assertEqual(len(self.stats_calls(calls)), t.STATS_REQUESTS_PER_RUN)
        self.assertNotIn(BETA, self.stats_calls(calls))
        _, calls = self.sync(league, stats, more_feeds={(BETA, 'leagueMatch'): beta})
        self.assertEqual(self.stats_calls(calls), ['709', BETA, '880'])

    def test_a_failed_rating_is_reported_and_retried_without_touching_matches(self):
        stats = {ALPHA: stats_reply(ALPHA, 1808), '900': t.EAError('EA returned HTTP 400.', code=400),
                 '901': stats_reply('901', 1400)}
        report, calls = self.sync([recent('9507', 10), recent('9508', 40, them='901')], stats)
        self.assertEqual(self.stats_calls(calls), [ALPHA, '900', '901'])
        club = report['clubs'][ALPHA]
        self.assertEqual(club['skill_ratings'], {'fetched': 2, 'errors': ['club 900: EA returned HTTP 400.']})
        self.assertEqual((club['status'], club['needs_attention'], report['needs_attention']), ('success', False, False))
        self.assertIn('[Alpha United (100)] skill ratings: ERROR club 900: EA returned HTTP 400.', t.status_lines(report))
        self.assertEqual(sorted(p.stem for p in (self.folder() / 'matches').glob('*.json')), ['9507', '9508'])
        stats['900'] = stats_reply('900', 1750)
        _, calls = self.sync([recent('9507', 10), recent('9508', 40, them='901')], stats)
        self.assertEqual(self.stats_calls(calls), ['900'])

    def test_a_reply_without_the_club_is_not_stored(self):
        stats = {ALPHA: stats_reply(ALPHA, 1808), '900': []}
        report, _ = self.sync([recent('9509', 10)], stats)
        self.assertEqual(report['clubs'][ALPHA]['skill_ratings']['errors'], ['club 900: EA returned no stats for club 900.'])
        envelopes = t.stats_envelopes(a.ArchiveStore(self.folder(), t.find_club(self.cfg, ALPHA)))
        self.assertEqual([e['club_id'] for e in envelopes], [ALPHA])
        self.assertIsNone(self.viewer()['9509']['skill']['opponent'])

    def test_access_denial_on_a_rating_pauses_every_club(self):
        self.cfg = f.config((ALPHA, BETA))
        stats = {ALPHA: t.EAError('EA returned HTTP 403.', code=403)}
        report, calls = self.sync([recent('9510', 10)], stats,
                                  more_feeds={(BETA, 'leagueMatch'): [recent('9811', 10, us=BETA)]})
        self.assertEqual(calls[-1], (ALPHA, 'overallStats'))
        self.assertFalse(any(club == BETA for club, _ in calls))
        self.assertTrue(t.read_json(self.root / 'data/collector_state.json')['halted'])
        alpha, beta = report['clubs'][ALPHA], report['clubs'][BETA]
        self.assertEqual((alpha['status'], alpha['failed_match_type'], alpha['needs_attention']), ('partial', 'skill ratings', True))
        self.assertEqual(beta['status'], 'blocked')
        self.assertTrue((self.folder() / 'matches' / '9510.json').is_file())  # Saved before the denial.

    def test_rate_limit_on_a_rating_sets_the_cooldown(self):
        stats = {ALPHA: t.EAError('EA returned HTTP 429.', code=429, retry_after='600')}
        self.sync([recent('9512', 10)], stats)
        report, calls = self.sync([recent('9512', 10)], stats)
        self.assertEqual((calls, report['status']), ([], 'cooldown'))

    def test_every_request_is_paced(self):
        pauses = []
        fetch, calls = f.fetcher({(ALPHA, 'leagueMatch'): [recent('9513', 10)]}, stats={ALPHA: stats_reply(ALPHA, 1808),
                                                                                          '900': stats_reply('900', 1700)})
        t.collect(self.root, self.cfg, fetch=fetch, pause=pauses.append)
        self.assertEqual(len(calls), 4)
        self.assertEqual(pauses, [t.REQUEST_PAUSE] * 3)

    def test_the_whole_reply_is_archived_and_protected(self):
        reply = stats_reply('900', 1758, [ALPHA], reputationtier='3')
        self.sync([recent('9514', 10)], {ALPHA: stats_reply(ALPHA, 1808), '900': reply})
        store = a.ArchiveStore(self.folder(), t.find_club(self.cfg, ALPHA))
        envelope = next(e for e in t.stats_envelopes(store) if e['club_id'] == '900')
        self.assertEqual(envelope['response'], reply)
        self.assertEqual(envelope['for'], [{'club_id': ALPHA, 'match_id': '9514'}])
        self.assertEqual(store.verify()['club_stats'], 2)
        path = next(p for p in (self.folder() / 'club_stats').glob('*.json') if a.load(p)['club_id'] == '900')
        edited = a.load(path); edited['response'][0]['skillRating'] = '9999'
        path.write_bytes(a.encoded(edited))
        with self.assertRaisesRegex(a.ArchiveError, 'Checksum mismatch'):
            store.verify()

    def test_stats_for_another_clubs_matches_are_refused(self):
        store = a.ArchiveStore(self.folder(), t.find_club(self.cfg, ALPHA))
        store.prepare()
        for envelope in ({'club_id': '900', 'fetched_at': 'x', 'response': [], 'for': [{'club_id': BETA, 'match_id': '1'}]},
                         {'club_id': '../900', 'fetched_at': 'x', 'response': [], 'for': [{'club_id': ALPHA, 'match_id': '1'}]},
                         {'club_id': '900', 'fetched_at': 'x', 'response': [], 'for': []}):
            with self.assertRaises(a.ArchiveError):
                store.club_stats(envelope)

    def test_all_match_csv_has_both_ratings(self):
        self.sync([recent('9515', 10)], {ALPHA: stats_reply(ALPHA, 1808, ['900']), '900': stats_reply('900', 1758, [ALPHA])})
        t.build_site(self.root, self.cfg)
        with (self.root / f'site/data/clubs/{ALPHA}/player_matches_{ALPHA}.csv').open(encoding='utf-8-sig', newline='') as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual((row['club_skill_rating'], row['opponent_skill_rating']), ('1808', '1758'))

    def test_real_transport_keeps_the_raw_http_reply(self):
        match = recent('9516', 10)

        class Response(io.BytesIO):
            status = 200
            headers = {'Content-Type': 'application/json'}
            def __enter__(self): return self
            def __exit__(self, *_): self.close()

        seen = []
        def fake_urlopen(request, timeout=25):
            url = urlsplit(request.full_url); query = parse_qs(url.query)
            seen.append(url.path.rsplit('/', 1)[-1])
            if url.path.endswith('/clubs/overallStats'):
                club = query['clubIds'][0]
                return Response(json.dumps(stats_reply(club, 1808 if club == ALPHA else 1700)).encode())
            return Response(json.dumps([match] if query['matchType'][0] == 'leagueMatch' else []).encode())

        with patch.object(t, 'urlopen', side_effect=fake_urlopen):
            report = t.collect(self.root, self.cfg, pause=lambda _: None)
        self.assertEqual(seen, ['matches', 'matches', 'overallStats', 'overallStats'])
        self.assertEqual(report['clubs'][ALPHA]['skill_ratings']['fetched'], 2)
        captured = [a.load(p) for p in (self.folder() / 'responses' / 'clubStats').glob('*.json')]
        self.assertEqual(sorted(json.loads(base64.b64decode(c['body_base64']))[0]['clubId'] for c in captured), [ALPHA, '900'])


if __name__ == '__main__':
    unittest.main()
