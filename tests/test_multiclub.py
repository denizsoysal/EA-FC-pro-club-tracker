"""Several clubs in one repository. Synthetic records and mocked requests only.

These tests do not contact EA and do not prove that any real club ID works.
"""
import copy
import csv
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

from test_tracker import t, config as legacy_config, match as legacy_match
import archive_store as a
import persist_archive as p
import multiclub_fixtures as f
from multiclub_fixtures import ALPHA, BETA, GAMMA

ROOT = Path(__file__).resolve().parents[1]


def tree(folder):
    """Every file under folder as {relative path: bytes}."""
    return {path.relative_to(folder).as_posix(): path.read_bytes()
            for path in sorted(folder.rglob('*')) if path.is_file()}


class TempRoot(unittest.TestCase):
    clubs = (ALPHA, BETA)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cfg = f.config(self.clubs)
        t.write_json(self.root / 'config.json', self.cfg)

    def tearDown(self):
        self.temp.cleanup()

    def folder(self, club_id):
        return self.root / 'data' / 'fc27' / 'common-gen5' / club_id

    def match_ids(self, club_id):
        return sorted(path.stem for path in (self.folder(club_id) / 'matches').glob('*.json'))

    def club(self, club_id):
        return next(club for club in t.configured_clubs(self.cfg) if club['club_id'] == club_id)


class ConfigTests(unittest.TestCase):
    def load(self, cfg):
        with tempfile.TemporaryDirectory() as directory:
            t.write_json(Path(directory) / 'config.json', cfg)
            return t.load_config(Path(directory))

    def test_legacy_single_club_config_still_works(self):
        cfg = legacy_config()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            t.write_json(root / 'config.json', cfg)
            loaded = t.load_config(root)
            self.assertEqual(loaded, cfg)
            clubs = t.configured_clubs(loaded)
            self.assertEqual([club['club_id'] for club in clubs], ['100'])
            self.assertEqual(clubs[0]['club_name'], 'Synthetic Club')
            self.assertEqual(clubs[0]['match_types'], cfg['match_types'])
            self.assertEqual(t.default_club(loaded)['club_id'], '100')
            calls = []
            def fetch(endpoint, params):
                calls.append((params['clubIds'], params['matchType']))
                return [legacy_match()] if params['matchType'] == 'leagueMatch' else []
            report = t.collect(root, loaded, fetch=fetch, pause=lambda _: None)
            self.assertEqual(calls, [('100', 'leagueMatch'), ('100', 'playoffMatch')])
            self.assertEqual(report['status'], 'success')
            self.assertTrue((root / 'data/fc27/common-gen5/100/matches/900.json').is_file())
            payloads = t.build_site(root, loaded, report)
            self.assertEqual(list(payloads), ['100'])
            catalog = t.read_json(root / 'site/data/clubs.json')
            self.assertEqual(catalog['default_club_id'], '100')
            self.assertEqual([club['club_id'] for club in catalog['clubs']], ['100'])

    def test_blank_legacy_club_is_still_a_setup_state(self):
        cfg = legacy_config(); cfg['club_id'] = ''
        loaded = self.load(cfg)
        self.assertEqual(t.configured_clubs(loaded), [])
        self.assertEqual(t.default_club(loaded)['club_id'], '')
        with tempfile.TemporaryDirectory() as directory:
            report = t.collect(Path(directory), loaded, fetch=lambda *_: self.fail('no request expected'))
            self.assertEqual(report['status'], 'setup_required')
            self.assertEqual(t.build_site(Path(directory), loaded), {})
            self.assertEqual(t.read_json(Path(directory) / 'site/data/clubs.json')['clubs'], [])

    def test_clubs_list_is_authoritative_over_legacy_top_level_fields(self):
        # The same club in both places, and a different club only at the top level.
        for top_level in (ALPHA, '999'):
            with self.subTest(top_level=top_level):
                cfg = f.config(club_id=top_level, club_name='Top level', platform='common-gen5', edition='fc27')
                loaded = self.load(cfg)
                self.assertEqual([club['club_id'] for club in t.configured_clubs(loaded)], [ALPHA, BETA])
                with tempfile.TemporaryDirectory() as directory:
                    _, calls = f.sync(Path(directory), loaded)
                self.assertEqual(calls, [(ALPHA, 'leagueMatch'), (ALPHA, 'playoffMatch'),
                                         (BETA, 'leagueMatch'), (BETA, 'playoffMatch')])

    def test_default_club_is_kept_and_must_be_configured(self):
        self.assertEqual(t.default_club(self.load(f.config()))['club_id'], ALPHA)
        self.assertEqual(t.default_club(self.load(f.config(default_club_id=BETA)))['club_id'], BETA)
        cfg = f.config(); del cfg['default_club_id']
        self.assertEqual(t.default_club(self.load(cfg))['club_id'], ALPHA)
        for bad in ('999', 'abc', True, '../200'):
            with self.subTest(default=bad):
                with self.assertRaisesRegex(ValueError, 'default_club_id'):
                    self.load(f.config(default_club_id=bad))

    def test_duplicate_club_identities_rejected(self):
        for second in (ALPHA, '0100', 100):
            with self.subTest(second=second):
                cfg = f.config(); cfg['clubs'][1]['club_id'] = second
                with self.assertRaisesRegex(ValueError, 'Duplicate club_id'):
                    self.load(cfg)
        # The ID alone is the selector key, so another platform does not make it distinct.
        cfg = f.config(); cfg['clubs'][1].update(club_id=ALPHA, platform='common-gen4')
        with self.assertRaisesRegex(ValueError, 'Duplicate club_id'):
            self.load(cfg)

    def test_invalid_club_entries_rejected(self):
        for bad in ('', None, True, '12a', '../200', '1/2', ' 5', 1.5, -5):
            with self.subTest(club_id=bad):
                cfg = f.config(); cfg['clubs'][1]['club_id'] = bad
                with self.assertRaisesRegex(ValueError, 'numeric EA club ID'):
                    self.load(cfg)
        for key in ('platform', 'edition'):
            with self.subTest(key=key):
                cfg = f.config(); cfg['clubs'][1][key] = '../../etc'
                with self.assertRaisesRegex(ValueError, f'Invalid {key} for club 200'):
                    self.load(cfg)
        for bad in ([], {}, 'x', [ALPHA], None):
            with self.subTest(clubs=bad):
                cfg = f.config(); cfg['clubs'] = bad
                with self.assertRaises(ValueError):
                    self.load(cfg)
        cfg = f.config(); cfg['clubs'][0]['club_name'] = 5
        with self.assertRaisesRegex(ValueError, 'club_name'):
            self.load(cfg)

    def test_club_entries_inherit_top_level_platform_and_edition(self):
        cfg = f.config(platform='common-gen5', edition='fc27')
        cfg['clubs'][1] = {'club_id': BETA}
        beta = t.configured_clubs(self.load(cfg))[1]
        self.assertEqual((beta['platform'], beta['edition'], beta['club_name']), ('common-gen5', 'fc27', 'Club 200'))
        del cfg['platform']
        with self.assertRaisesRegex(ValueError, 'Invalid platform for club 200'):
            self.load(cfg)

    def test_shared_settings_reach_every_club_unchanged(self):
        cfg = f.config(collection_enabled=False, advanced_mapping_version=t.DECODER, advanced_mapping_confirmed=True)
        for club in t.configured_clubs(self.load(cfg)):
            self.assertIs(club['collection_enabled'], False)
            self.assertEqual(club['match_types'], ['leagueMatch', 'playoffMatch'])
            self.assertTrue(t.mapping_reviewed(club))

    @unittest.skipUnless((ROOT / 'config.json').is_file(), 'this package has no config.json')
    def test_repository_configuration_is_valid(self):
        cfg = t.load_config(ROOT)
        clubs = t.configured_clubs(cfg)
        self.assertIn(t.default_club(cfg)['club_id'], [club['club_id'] for club in clubs])


class CollectionTests(TempRoot):
    def test_each_club_is_requested_exactly_once_per_match_type(self):
        report, calls = f.sync(self.root, self.cfg)
        self.assertEqual(calls, [(ALPHA, 'leagueMatch'), (ALPHA, 'playoffMatch'),
                                 (BETA, 'leagueMatch'), (BETA, 'playoffMatch')])
        self.assertEqual(report['status'], 'success')
        self.assertFalse(report['needs_attention'])
        self.assertEqual(list(report['clubs']), [ALPHA, BETA])

    def test_matches_are_stored_under_each_clubs_own_path(self):
        f.sync(self.root, self.cfg)
        self.assertEqual(self.match_ids(ALPHA), ['9001', '9002', '9003'])
        self.assertEqual(self.match_ids(BETA), ['9001', '9004'])
        for club_id in (ALPHA, BETA):
            for path in (self.folder(club_id) / 'matches').glob('*.json'):
                record = a.load(path)
                self.assertEqual((record['edition'], record['platform'], record['club_id'], record['match_id']),
                                 ('fc27', 'common-gen5', club_id, path.stem))
            for group in ('revisions', 'snapshots'):
                self.assertTrue(any((self.folder(club_id) / group).rglob('*.json')), group)
            self.assertTrue((self.folder(club_id) / 'integrity.json').is_file())

    def test_repeated_sync_changes_nothing_and_duplicates_nothing(self):
        f.sync(self.root, self.cfg)
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (self.root / 'data').rglob('*.json')}
        report, _ = f.sync(self.root, self.cfg)
        after = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (self.root / 'data').rglob('*.json')}
        self.assertEqual(before, after)
        for club in report['clubs'].values():
            self.assertTrue(all(result['changed'] == 0 for result in club['results'].values()))
        self.assertEqual(len(t.build_site(self.root, self.cfg)[ALPHA]['matches']), 3)

    def test_same_match_id_lives_independently_in_both_archives(self):
        f.sync(self.root, self.cfg)
        alpha, beta = (self.folder(club) / 'matches' / '9001.json' for club in (ALPHA, BETA))
        self.assertEqual(a.load(alpha)['raw'], f.DERBY)
        self.assertEqual(a.load(beta)['raw'], f.DERBY)
        self.assertEqual((a.load(alpha)['club_id'], a.load(beta)['club_id']), (ALPHA, BETA))
        # A later revision seen through one club's feed leaves the other club's copy alone.
        before = tree(self.folder(ALPHA))
        revised = copy.deepcopy(f.DERBY); revised['players'][BETA]['601']['rating'] = '9.9'
        t.save_matches(self.root, self.club(BETA), 'leagueMatch', [revised])
        self.assertEqual(tree(self.folder(ALPHA)), before)
        self.assertEqual(a.load(beta)['raw'], revised)
        self.assertEqual(len(list((self.folder(BETA) / 'revisions' / '9001').glob('*.json'))), 2)

    def test_raw_archive_keeps_both_teams_and_unknown_fields(self):
        derby = copy.deepcopy(f.DERBY)
        derby['unrecognised_future_field'] = {'keep': [1, None, 'é ⚽']}
        derby['players'][BETA]['600']['match_event_aggregate_9'] = '9999:3'
        feeds = {**f.FEEDS, (ALPHA, 'leagueMatch'): [derby]}
        f.sync(self.root, self.cfg, feeds=feeds)
        raw = a.load(self.folder(ALPHA) / 'matches' / '9001.json')['raw']
        self.assertEqual(raw, derby)
        self.assertEqual(sorted(raw['players']), [ALPHA, BETA])
        # The viewer dataset filters to Alpha's players; the archive above does not.
        shown = t.build(self.root, self.club(ALPHA))['matches'][-1]
        self.assertEqual([player['name'] for player in shown['players']], ['Shared Striker', 'Alpha Anchor'])

    def test_derby_is_seen_from_opposite_correct_perspectives(self):
        f.sync(self.root, self.cfg)
        views = {club: next(m for m in payload['matches'] if m['id'] == '9001')
                 for club, payload in t.build_site(self.root, self.cfg).items()}
        alpha, beta = views[ALPHA], views[BETA]
        self.assertEqual((alpha['opponent'], alpha['goals'], alpha['goals_against'], alpha['result']),
                         ('Beta Town', 3, 1, 'W'))
        self.assertEqual((beta['opponent'], beta['goals'], beta['goals_against'], beta['result']),
                         ('Alpha United', 1, 3, 'L'))
        self.assertEqual([player['id'] for player in alpha['players']], ['500', '501'])
        self.assertEqual([player['id'] for player in beta['players']], ['600', '601'])

    def test_player_in_both_clubs_has_separate_statistics(self):
        f.sync(self.root, self.cfg)
        payloads = t.build_site(self.root, self.cfg)
        def goals(club_id, player_id):
            return [player['goals'] for match in payloads[club_id]['matches']
                    for player in match['players'] if player['id'] == player_id]
        self.assertEqual(sorted(goals(ALPHA, '500')), [0, 0, 1])
        self.assertEqual(goals(BETA, '500'), [3])
        for club_id, expected in ((ALPHA, 3), (BETA, 1)):
            with (self.root / f'site/data/clubs/{club_id}/player_matches_{club_id}.csv').open(
                    encoding='utf-8-sig', newline='') as handle:
                rows = [row for row in csv.DictReader(handle) if row['player_id'] == '500']
            self.assertEqual(len(rows), expected)
            self.assertEqual({row['club_id'] for row in rows}, {club_id})

    def test_empty_and_null_feeds_never_delete_history(self):
        f.sync(self.root, self.cfg)
        before = {club: tree(self.folder(club) / 'matches') for club in (ALPHA, BETA)}
        for empty in ([], None):
            report, calls = f.sync(self.root, self.cfg, feeds={key: empty for key in f.FEEDS})
            self.assertEqual(len(calls), 4)
            self.assertEqual(report['status'], 'success')
        for club in (ALPHA, BETA):
            self.assertEqual(tree(self.folder(club) / 'matches'), before[club])
            self.assertEqual(t.archive_report(self.root, self.cfg)['status'], 'ok')

    def test_one_clubs_failure_neither_stops_nor_discards_the_other(self):
        failures = {(ALPHA, 'leagueMatch'): t.EAError('EA returned HTTP 500.', code=500)}
        report, calls = f.sync(self.root, self.cfg, failures=failures)
        # Alpha's second feed is skipped after its failure; Beta is still collected in full.
        self.assertEqual(calls, [(ALPHA, 'leagueMatch'), (BETA, 'leagueMatch'), (BETA, 'playoffMatch')])
        self.assertEqual(self.match_ids(BETA), ['9001', '9004'])
        self.assertEqual(self.match_ids(ALPHA), [])
        alpha, beta = report['clubs'][ALPHA], report['clubs'][BETA]
        self.assertEqual((alpha['status'], alpha['failed_match_type'], alpha['error']),
                         ('error', 'leagueMatch', 'EA returned HTTP 500.'))
        self.assertEqual((beta['status'], beta['error'], beta['needs_attention']), ('success', None, False))
        self.assertEqual(beta['results']['leagueMatch']['received'], 2)
        self.assertEqual(beta['results']['leagueMatch']['changed'], 2)
        self.assertEqual((report['status'], report['needs_attention']), ('partial', True))
        self.assertIn('Alpha United (100): EA returned HTTP 500.', report['error'])
        lines = t.status_lines(report)
        self.assertIn('[Alpha United (100)] leagueMatch: ERROR EA returned HTTP 500.', lines)
        self.assertIn('[Beta Town (200)] leagueMatch: received 2, changed 2', lines)
        self.assertIn('[Beta Town (200)] playoffMatch: received 0, changed 0', lines)
        # Each club's published status is its own.
        payloads = t.build_site(self.root, self.cfg, report)
        self.assertEqual(payloads[ALPHA]['collection']['status'], 'error')
        self.assertEqual(payloads[BETA]['collection']['status'], 'success')

    def test_a_later_failure_keeps_the_failing_clubs_earlier_history(self):
        f.sync(self.root, self.cfg)
        before = tree(self.folder(ALPHA) / 'matches')
        failures = {(ALPHA, 'leagueMatch'): t.EAError('EA returned invalid JSON or an HTML page; no data imported.')}
        report, calls = f.sync(self.root, self.cfg, failures=failures)
        self.assertEqual(tree(self.folder(ALPHA) / 'matches'), before)
        self.assertEqual(calls[-2:], [(BETA, 'leagueMatch'), (BETA, 'playoffMatch')])
        self.assertEqual(report['clubs'][ALPHA]['last_api_attempt_at'] is not None, True)
        self.assertEqual(t.archive_report(self.root, self.cfg)['status'], 'ok')

    def test_rejected_feed_is_kept_as_a_snapshot_and_other_club_continues(self):
        wrong = copy.deepcopy(f.BETA_WIN)  # A feed for Alpha that does not contain Alpha.
        report, calls = f.sync(self.root, self.cfg, feeds={**f.FEEDS, (ALPHA, 'leagueMatch'): [wrong]})
        self.assertEqual(report['clubs'][ALPHA]['status'], 'error')
        self.assertIn('does not contain the configured club', report['clubs'][ALPHA]['error'])
        self.assertEqual(self.match_ids(ALPHA), [])
        snapshots = [a.load(path) for path in (self.folder(ALPHA) / 'snapshots').rglob('*.json')]
        self.assertIn([wrong], snapshots)
        self.assertEqual(self.match_ids(BETA), ['9001', '9004'])

    def test_access_denial_is_host_wide_and_not_bypassed_by_the_next_club(self):
        failures = {(ALPHA, 'playoffMatch'): t.EAError('EA returned HTTP 403.', code=403)}
        report, calls = f.sync(self.root, self.cfg, failures=failures)
        self.assertEqual(calls, [(ALPHA, 'leagueMatch'), (ALPHA, 'playoffMatch')])
        # Alpha's successful first feed was persisted before the denial was reported.
        self.assertEqual(self.match_ids(ALPHA), ['9001', '9002'])
        self.assertEqual(report['clubs'][ALPHA]['status'], 'partial')
        beta = report['clubs'][BETA]
        self.assertEqual((beta['status'], beta['needs_attention'], beta['last_api_attempt_at']), ('blocked', True, None))
        self.assertIn('Alpha United (100)', beta['message'])
        self.assertTrue(t.read_json(self.root / 'data/collector_state.json')['halted'])
        report, calls = f.sync(self.root, self.cfg, event='schedule')
        self.assertEqual((calls, report['status']), ([], 'blocked'))
        self.assertTrue(t.build(self.root, self.club(BETA))['persistent_state']['halted'])
        report, calls = f.sync(self.root, self.cfg, resume=True)
        self.assertEqual((len(calls), report['status']), (4, 'success'))
        self.assertFalse(t.access_state(self.root)['halted'])

    def test_rate_limit_cooldown_applies_to_every_club(self):
        failures = {(ALPHA, 'leagueMatch'): t.EAError('EA returned HTTP 429.', code=429, retry_after='3600')}
        report, calls = f.sync(self.root, self.cfg, failures=failures)
        self.assertEqual(calls, [(ALPHA, 'leagueMatch')])
        self.assertEqual(report['clubs'][BETA]['status'], 'cooldown')
        for options in ({}, {'resume': True}, {'event': 'schedule'}):
            report, calls = f.sync(self.root, self.cfg, **options)
            self.assertEqual((calls, report['status']), ([], 'cooldown'))

    def test_pause_recorded_by_a_single_club_version_blocks_every_club(self):
        state = self.folder(BETA) / 'collector_state.json'
        t.write_json(state, {'halted': True, 'reason': 'HTTP 403', 'cooldown_until': None})
        report, calls = f.sync(self.root, self.cfg)
        self.assertEqual((calls, report['status']), ([], 'blocked'))
        self.assertEqual({club['status'] for club in report['clubs'].values()}, {'blocked'})
        report, calls = f.sync(self.root, self.cfg, resume=True)
        self.assertEqual(len(calls), 4)
        self.assertFalse(t.read_json(state)['halted'])

    def test_unreachable_host_skips_remaining_clubs_without_a_lasting_pause(self):
        failures = {(ALPHA, 'leagueMatch'): t.EAError('EA could not be reached: timed out', unreachable=True)}
        report, calls = f.sync(self.root, self.cfg, failures=failures)
        self.assertEqual(calls, [(ALPHA, 'leagueMatch')])
        self.assertEqual((report['clubs'][BETA]['status'], report['clubs'][BETA]['needs_attention']), ('skipped', True))
        self.assertEqual(report['status'], 'error')
        report, calls = f.sync(self.root, self.cfg)
        self.assertEqual((len(calls), report['status']), (4, 'success'))

    def test_every_request_after_the_first_is_paced(self):
        pauses = []
        fetch, calls = f.fetcher()
        t.collect(self.root, self.cfg, fetch=fetch, pause=pauses.append)
        self.assertEqual(len(calls), 4)
        self.assertEqual(pauses, [t.REQUEST_PAUSE] * 3)
        self.assertGreaterEqual(t.REQUEST_PAUSE, 2)

    def test_gap_detection_and_timestamps_stay_with_their_club(self):
        f.sync(self.root, self.cfg)
        unseen = [f.fixture(str(9100 + n), 10 + n, (ALPHA, 'Alpha United', 1, {}), ('950', 'Other', 0, {}))
                  for n in range(10)]
        report, _ = f.sync(self.root, self.cfg, feeds={**f.FEEDS, (ALPHA, 'leagueMatch'): unseen})
        alpha, beta = report['clubs'][ALPHA], report['clubs'][BETA]
        self.assertTrue(alpha['results']['leagueMatch']['possible_gap'])
        self.assertTrue(alpha['needs_attention'])
        self.assertIn('possible missing matches', alpha['message'])
        self.assertFalse(beta['results']['leagueMatch']['possible_gap'])
        self.assertFalse(beta['needs_attention'])
        self.assertNotIn('possible missing matches', beta['message'])
        self.assertIsNotNone(beta['last_api_attempt_at'])

    def test_schedule_respects_the_disabled_switch_for_every_club(self):
        cfg = f.config(collection_enabled=False)
        report, calls = f.sync(self.root, cfg, event='schedule')
        self.assertEqual((calls, report['status']), ([], 'disabled'))
        self.assertEqual({club['status'] for club in report['clubs'].values()}, {'disabled'})
        report, calls = f.sync(self.root, cfg)  # A deliberate manual run still works.
        self.assertEqual((len(calls), report['status']), (4, 'success'))
        report, calls = f.sync(self.root, cfg, event='push')
        self.assertEqual((calls, report['status']), ([], 'not_requested'))

    def test_damaged_club_is_not_requested_but_the_other_club_is(self):
        f.sync(self.root, self.cfg)
        damaged = self.folder(ALPHA) / 'matches' / '9002.json'
        damaged.write_text('broken')
        report, calls = f.sync(self.root, self.cfg)
        self.assertEqual(calls, [(BETA, 'leagueMatch'), (BETA, 'playoffMatch')])
        self.assertEqual(report['clubs'][ALPHA]['status'], 'error')
        self.assertIn('Archive integrity check failed', report['clubs'][ALPHA]['message'])
        self.assertEqual(report['clubs'][BETA]['status'], 'success')
        self.assertEqual(damaged.read_text(), 'broken')


class IntegrityAndBackupTests(TempRoot):
    def test_verify_reports_each_club_and_flags_only_the_damaged_one(self):
        f.sync(self.root, self.cfg)
        report = t.archive_report(self.root, self.cfg)
        self.assertEqual(report['status'], 'ok')
        self.assertEqual([(entry['club_id'], entry['club_name'], entry['matches'], entry['path'])
                          for entry in report['archives']],
                         [(ALPHA, 'Alpha United', 3, 'data/fc27/common-gen5/100'),
                          (BETA, 'Beta Town', 2, 'data/fc27/common-gen5/200')])
        record = a.load(self.folder(BETA) / 'matches' / '9004.json'); record['raw']['extra'] = 'tampered'
        (self.folder(BETA) / 'matches' / '9004.json').write_bytes(a.encoded(record))
        report = t.archive_report(self.root, self.cfg)
        alpha, beta = report['archives']
        self.assertEqual((report['status'], alpha['status'], beta['status']), ('error', 'ok', 'error'))
        self.assertIn('Checksum mismatch: matches/9004.json', beta['error'])
        with self.assertRaisesRegex(a.ArchiveError, r'club 200 .*Checksum mismatch') as caught:
            t.archive_check(self.root, self.cfg)
        self.assertEqual(caught.exception.report, report)

    def test_deleted_file_in_one_club_is_detected(self):
        f.sync(self.root, self.cfg)
        (self.folder(ALPHA) / 'matches' / '9003.json').unlink()
        alpha, beta = t.archive_report(self.root, self.cfg)['archives']
        self.assertIn('Missing archived file(s): matches/9003.json', alpha['error'])
        self.assertEqual(beta['status'], 'ok')

    def test_new_club_gets_a_baseline_without_resetting_the_existing_one(self):
        single = f.config((ALPHA,))
        f.sync(self.root, single)
        before = tree(self.folder(ALPHA))
        self.assertFalse(self.folder(BETA).exists())
        # Verify asks for the missing baseline instead of inventing a healthy result.
        alpha, beta = t.archive_report(self.root, self.cfg)['archives']
        self.assertEqual(alpha['status'], 'ok')
        self.assertIn('no checksum baseline yet', beta['error'])
        report = t.archive_check(self.root, self.cfg, protect=True)
        self.assertEqual([(entry['club_id'], entry['matches']) for entry in report['archives']], [(ALPHA, 3), (BETA, 0)])
        self.assertEqual(a.load(self.folder(BETA) / 'integrity.json')['identity']['club_id'], BETA)
        self.assertEqual(tree(self.folder(ALPHA)), before)

    def test_missing_inventory_of_a_versioned_club_is_not_recreated(self):
        f.sync(self.root, self.cfg)
        (self.folder(BETA) / 'integrity.json').unlink()
        for protect in (False, True):
            alpha, beta = t.archive_report(self.root, self.cfg, protect=protect)['archives']
            self.assertEqual((alpha['status'], beta['status']), ('ok', 'error'))
        self.assertIn('inventory is missing', beta['error'])
        self.assertFalse((self.folder(BETA) / 'integrity.json').exists())
        report, calls = f.sync(self.root, self.cfg)
        self.assertEqual(calls, [(ALPHA, 'leagueMatch'), (ALPHA, 'playoffMatch')])
        self.assertFalse((self.folder(BETA) / 'integrity.json').exists())

    def test_backup_contains_both_clubs_and_restores_verified(self):
        f.sync(self.root, self.cfg)
        info = a.create_backup(self.root)
        self.assertEqual([(club['club_id'], club['matches']) for club in info['clubs']], [(ALPHA, 3), (BETA, 2)])
        data = tree(self.root / 'data')
        with zipfile.ZipFile(info['backup']) as archive:
            for relative, body in data.items():
                self.assertEqual(archive.read('data/' + relative), body)
            self.assertEqual(json.loads(archive.read('config.json')), self.cfg)
        restored = self.root / 'restored'; restored.mkdir()
        with zipfile.ZipFile(info['backup']) as archive:
            archive.extractall(restored)
        report = t.archive_check(restored, t.load_config(restored))
        self.assertEqual([(entry['club_id'], entry['matches']) for entry in report['archives']], [(ALPHA, 3), (BETA, 2)])
        self.assertEqual(a.verify_backup(Path(info['backup']))['status'], 'ok')


# The real CLI with the stdlib transport patched before tracker imports it.
MOCK_HTTP_SYNC = r'''
import io, runpy, sys, time
import urllib.request
from urllib.parse import parse_qs, urlsplit
from pathlib import Path

class Response(io.BytesIO):
    status = 200
    headers = {'Content-Type': 'application/json'}

def fake_urlopen(request, timeout=25):
    parsed = urlsplit(request.full_url)
    assert parsed.hostname == 'proclubs.ea.com', request.full_url
    query = parse_qs(parsed.query)
    reply = Path('reply_%s_%s.json' % (query['clubIds'][0], query['matchType'][0]))
    with open('requests.log', 'a') as log:
        log.write(reply.name + '\n')
    return Response(reply.read_bytes() if reply.exists() else b'[]')

urllib.request.urlopen = fake_urlopen
time.sleep = lambda seconds: None
sys.argv = ['scripts/tracker.py', 'sync']
runpy.run_path('scripts/tracker.py', run_name='__main__')
'''


class UpgradeToSeveralClubsTests(unittest.TestCase):
    def test_existing_single_club_archive_is_byte_identical_after_adding_a_club(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / 'scripts', root / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copytree(ROOT / 'site', root / 'site', ignore=shutil.ignore_patterns('data'))

            def cli(*args, mocked=False, expect=0):
                command = [sys.executable, '-c', MOCK_HTTP_SYNC] if mocked else [sys.executable, 'scripts/tracker.py', *args]
                result = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=60)
                self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
                return result

            # A single-club installation in the pre-upgrade shape, with its own baseline.
            old = legacy_config(); old.update(club_name='Alpha United', custom_preference='KEEP EXACTLY')
            (root / 'config.json').write_bytes((json.dumps(old, indent=4) + '\n').encode('utf-8'))
            matches = root / 'data/fc27/common-gen5/100/matches'; matches.mkdir(parents=True)
            for n in range(10):
                mid = str(900 + n); raw = legacy_match(mid); raw['keep_future_field'] = {'unmapped': n, 'value': None}
                record = dict(schema_version=1, edition='fc27', platform='common-gen5', club_id='100', match_id=mid,
                              match_types=['leagueMatch'], first_archived_at='2026-09-28T13:35:55+00:00',
                              last_changed_at='2026-09-28T13:35:55+00:00', raw=raw)
                (matches / (mid + '.json')).write_bytes((json.dumps(record, indent=4) + '\n').encode('utf-8'))
            cli('backup')
            alpha = root / 'data/fc27/common-gen5/100'
            before = tree(alpha)
            self.assertEqual(sum(name.startswith('matches/') for name in before), 10)

            # Upgrade: the legacy fields stay and a clubs list is added beside them.
            new = dict(old, default_club_id='100', clubs=[
                {'club_id': '100', 'club_name': 'Alpha United', 'platform': 'common-gen5', 'edition': 'fc27'},
                {'club_id': '200', 'club_name': 'Beta Town', 'platform': 'common-gen5', 'edition': 'fc27'}])
            config_bytes = (json.dumps(new, indent=4) + '\n').encode('utf-8')
            (root / 'config.json').write_bytes(config_bytes)
            cli('build')
            self.assertEqual(tree(alpha), before)
            self.assertFalse((root / 'data/fc27/common-gen5/200').exists())
            self.assertIn('no checksum baseline yet', cli('verify', expect=1).stderr)
            backup = json.loads(cli('backup').stdout)
            self.assertEqual(tree(alpha), before)
            verified = json.loads(cli('verify').stdout)
            self.assertEqual([(entry['club_id'], entry['status'], entry['matches']) for entry in verified['archives']],
                             [('100', 'ok', 10), ('200', 'ok', 0)])
            self.assertEqual(tree(alpha), before)

            # One mocked run collects both clubs. Alpha's feed is empty this time.
            (root / 'reply_200_leagueMatch.json').write_text(json.dumps([f.DERBY, f.BETA_WIN]), encoding='utf-8')
            output = cli(mocked=True).stdout
            self.assertEqual((root / 'requests.log').read_text().split(),
                             ['reply_100_leagueMatch.json', 'reply_100_playoffMatch.json',
                              'reply_200_leagueMatch.json', 'reply_200_playoffMatch.json'])
            self.assertIn('[Alpha United (100)] leagueMatch: received 0, changed 0', output)
            self.assertIn('[Beta Town (200)] leagueMatch: received 2, changed 2', output)
            self.assertIn('Viewer contains 10 distinct archived matches for Alpha United (100).', output)
            self.assertIn('Viewer contains 2 distinct archived matches for Beta Town (200).', output)
            after = tree(alpha)
            # Nothing that existed before was rewritten or removed; only the inventory grew.
            for name, body in before.items():
                if name != 'integrity.json':
                    self.assertEqual(after[name], body, name)
            old_inventory, new_inventory = (json.loads(tree_[('integrity.json')])['files'] for tree_ in (before, after))
            self.assertEqual({name: new_inventory[name] for name in old_inventory}, old_inventory)
            self.assertEqual(sorted(path.stem for path in (root / 'data/fc27/common-gen5/200/matches').glob('*.json')),
                             ['9001', '9004'])
            verified = json.loads(cli('verify').stdout)
            self.assertEqual([(entry['club_id'], entry['status'], entry['matches']) for entry in verified['archives']],
                             [('100', 'ok', 10), ('200', 'ok', 2)])
            self.assertEqual((root / 'config.json').read_bytes(), config_bytes)
            with zipfile.ZipFile(backup['backup']) as archive:
                for name, body in before.items():
                    self.assertEqual(archive.read('data/fc27/common-gen5/100/' + name), body)
            # Deletion is still detected for the original club, and named per club.
            (matches / '905.json').unlink()
            failed = cli('verify', expect=1)
            self.assertIn('Missing archived file(s): matches/905.json', failed.stderr)
            report = {entry['club_id']: entry['status'] for entry in json.loads(failed.stdout)['archives']}
            self.assertEqual(report, {'100': 'error', '200': 'ok'})


class SiteBuildTests(TempRoot):
    clubs = (ALPHA, BETA, GAMMA)

    def build(self, **extra):
        self.cfg = f.config(self.clubs, **extra)
        report, _ = f.sync(self.root, self.cfg)
        return t.build_site(self.root, self.cfg, report)

    def test_each_club_gets_its_own_dataset_plus_one_catalog(self):
        payloads = self.build()
        self.assertEqual(sorted(tree(self.root / 'site/data')), [
            'clubs.json',
            'clubs/100/index.json', 'clubs/100/player_matches_100.csv',
            'clubs/200/index.json', 'clubs/200/player_matches_200.csv',
            'clubs/300/index.json', 'clubs/300/player_matches_300.csv'])
        catalog = t.read_json(self.root / 'site/data/clubs.json')
        self.assertEqual(catalog['default_club_id'], ALPHA)
        self.assertEqual(catalog['clubs'], [
            {'club_id': cid, 'club_name': f.NAMES[cid], 'platform': 'common-gen5', 'edition': 'fc27'}
            for cid in self.clubs])
        for club_id, count in ((ALPHA, 3), (BETA, 2), (GAMMA, 0)):
            stored = t.read_json(self.root / f'site/data/clubs/{club_id}/index.json')
            self.assertEqual(stored, payloads[club_id])
            self.assertEqual((stored['club']['club_id'], len(stored['matches'])), (club_id, count))
        self.assertEqual(payloads[GAMMA]['archive_updated_at'], None)

    def test_stale_single_club_outputs_are_removed(self):
        for name in ('index.json', 'player_matches.csv'):
            (self.root / 'site/data').mkdir(parents=True, exist_ok=True)
            (self.root / 'site/data' / name).write_text('stale single-club output')
        self.build()
        self.assertFalse((self.root / 'site/data/index.json').exists())
        self.assertFalse((self.root / 'site/data/player_matches.csv').exists())

    def test_published_site_has_no_private_configuration_or_backups(self):
        shutil.copytree(ROOT / 'site', self.root / 'site', ignore=shutil.ignore_patterns('data'))
        self.build(custom_preference='PRIVATE-MARKER')
        t.write_json(self.root / 'config.json', self.cfg)
        a.create_backup(self.root)
        published = tree(self.root / 'site')
        self.assertFalse([name for name in published if name.endswith('.zip') or 'config' in name])
        for name, body in published.items():
            self.assertNotIn(b'PRIVATE-MARKER', body, name)
            self.assertNotIn(b'collection_enabled', body, name)
            self.assertNotIn(b'advanced_mapping_confirmed', body, name)
        self.assertEqual(sorted(t.read_json(self.root / 'site/data/clubs/100/index.json')['club']),
                         ['club_id', 'club_name', 'edition', 'platform'])

    def test_static_urls_are_relative_for_a_repository_subpath(self):
        self.build()
        html = (ROOT / 'site/index.html').read_text(encoding='utf-8')
        links = re.findall(r'(?:href|src)="([^"]*)"', html)
        self.assertIn('assets/clubs.js?v=5', links)
        for link in links:
            self.assertFalse(link.startswith('/') or '://' in link, link)
        for script in ('app.js', 'clubs.js'):
            source = (ROOT / 'site/assets' / script).read_text(encoding='utf-8')
            self.assertFalse(re.search(r'''["'`]/(?:data|assets)''', source), script)
        self.assertIn('`data/clubs/${', (ROOT / 'site/assets/clubs.js').read_text(encoding='utf-8'))
        self.assertIn('getJson("data/clubs.json")', (ROOT / 'site/assets/app.js').read_text(encoding='utf-8'))
        # Generated data carries identities only; the viewer derives paths from validated IDs.
        self.assertNotIn('/', json.dumps(t.read_json(self.root / 'site/data/clubs.json')['clubs']))

    def test_all_match_csv_names_and_contains_its_club(self):
        self.build()
        path = self.root / 'site/data/clubs/200/player_matches_200.csv'
        with path.open(encoding='utf-8-sig', newline='') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual({(row['club_id'], row['club_name']) for row in rows}, {('200', 'Beta Town')})
        self.assertEqual(sorted({row['match_id'] for row in rows}), ['9001', '9004'])
        self.assertEqual(sorted({row['player_name'] for row in rows}), ['Beta Keeper', 'Beta Runner', 'Shared Striker'])

    def test_one_clubs_build_failure_still_builds_the_others_then_fails(self):
        self.build()
        (self.folder(BETA) / 'matches' / '9004.json').write_text('broken')
        shutil.rmtree(self.root / 'site/data')
        with self.assertRaisesRegex(ValueError, r'Beta Town \(200\)'):
            t.build_site(self.root, self.cfg)
        self.assertEqual(len(t.read_json(self.root / 'site/data/clubs/100/index.json')['matches']), 3)
        self.assertFalse((self.root / 'site/data/clubs/200/index.json').exists())

    def test_dataset_path_needs_a_validated_numeric_club(self):
        for bad in ('', '../200', '1/2'):
            with self.assertRaises(ValueError):
                t.dataset_dir(self.root, {'club_id': bad})

    def test_workflow_collects_every_club_in_the_single_existing_job(self):
        workflows = sorted(path.name for path in (ROOT / '.github/workflows').iterdir())
        self.assertEqual(workflows, ['archive-and-publish.yml'])
        workflow = (ROOT / '.github/workflows/archive-and-publish.yml').read_text(encoding='utf-8')
        self.assertIn("cron: '7,22,37,52 * * * *'", workflow)
        self.assertEqual(workflow.count('scripts/tracker.py sync'), 1)
        self.assertEqual(workflow.count('scripts/persist_archive.py'), 1)
        self.assertLess(workflow.index('scripts/tracker.py sync'), workflow.index('scripts/persist_archive.py'))
        self.assertLess(workflow.index('scripts/persist_archive.py'), workflow.index('actions/deploy-pages'))
        self.assertIn('cancel-in-progress: false', workflow)
        self.assertIn('path: site', workflow)
        self.assertIn('tests/test_clubs.cjs', workflow)
        self.assertNotIn('--force', workflow)


@unittest.skipUnless(shutil.which('git'), 'Git is required for local persistence integration tests')
class SeveralClubsPersistenceTests(unittest.TestCase):
    """Against a LOCAL bare Git remote, not the GitHub service."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.remote = self.base / 'remote.git'
        self.work = self.base / 'work'; self.work.mkdir()
        subprocess.run(['git', 'init', '--bare', str(self.remote)], check=True, capture_output=True)
        p.git(self.work, 'init', '-b', 'main')
        self.cfg = f.config()
        t.write_json(self.work / 'config.json', self.cfg)
        (self.work / '.gitignore').write_text('.runtime/\nsite/data/\nbackups/\n')
        shutil.copyfile(ROOT / '.gitattributes', self.work / '.gitattributes')
        f.sync(self.work, f.config((ALPHA,)))
        p.git(self.work, 'add', '.')
        p.git(self.work, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'Alpha only')
        p.git(self.work, 'remote', 'add', 'origin', str(self.remote))
        p.git(self.work, 'push', '-u', 'origin', 'main')

    def tearDown(self): self.tmp.cleanup()

    def clone(self, name):
        folder = self.base / name
        subprocess.run(['git', 'clone', '--branch', 'main', str(self.remote), str(folder)], check=True, capture_output=True)
        return folder

    def archives(self, folder):
        return {entry['club_id']: entry for entry in t.archive_report(folder, self.cfg)['archives']}

    def test_both_clubs_reach_the_remote_in_one_normal_push(self):
        before = p.git(self.work, 'rev-list', '--count', 'HEAD').stdout.strip()
        f.sync(self.work, self.cfg)
        result = p.persist(self.work, 'main', pause=lambda _: None)
        self.assertEqual((result['status'], result['new_commit']), ('pushed', True))
        self.assertEqual(int(p.git(self.work, 'rev-list', '--count', 'HEAD').stdout), int(before) + 1)
        remote = self.archives(self.clone('check'))
        self.assertEqual({club: (entry['status'], entry['matches']) for club, entry in remote.items()},
                         {ALPHA: ('ok', 3), BETA: ('ok', 2)})
        self.assertEqual([club['club_id'] for club in a.verify_backup(Path(result['recovery_zip']))['clubs']], [ALPHA, BETA])

    def test_push_event_initialises_a_new_clubs_baseline_without_touching_the_old_one(self):
        alpha = self.work / 'data/fc27/common-gen5' / ALPHA
        before = tree(alpha)
        result = p.persist(self.work, 'main', pause=lambda _: None)
        self.assertTrue(result['new_commit'])
        self.assertEqual(tree(alpha), before)
        remote = self.archives(self.clone('check'))
        self.assertEqual({club: (entry['status'], entry['matches']) for club, entry in remote.items()},
                         {ALPHA: ('ok', 3), BETA: ('ok', 0)})

    def test_damaged_club_does_not_strand_the_other_clubs_new_matches(self):
        f.sync(self.work, self.cfg)
        p.persist(self.work, 'main', pause=lambda _: None)
        damaged = self.work / 'data/fc27/common-gen5' / BETA / 'matches' / '9004.json'
        original = damaged.read_bytes()
        damaged.write_bytes(original.replace(b'Beta Runner', b'Edited Name'))
        new_match = f.fixture('9009', 9, (ALPHA, 'Alpha United', 2, {}), ('960', 'Late Opponent', 0, {}))
        t.save_matches(self.work, t.configured_clubs(self.cfg)[0], 'leagueMatch', [new_match])
        with self.assertRaisesRegex(a.ArchiveError, r'committed and pushed\. NOT committed.*club 200 .*Checksum mismatch'):
            p.persist(self.work, 'main', pause=lambda _: None)
        final = self.clone('final')
        remote = self.archives(final)
        self.assertEqual((remote[ALPHA]['status'], remote[ALPHA]['matches']), ('ok', 4))
        # The damage was neither committed nor repaired behind anyone's back.
        self.assertEqual((final / 'data/fc27/common-gen5' / BETA / 'matches' / '9004.json').read_bytes(), original)
        self.assertEqual(remote[BETA]['status'], 'ok')
        self.assertNotEqual(damaged.read_bytes(), original)
        self.assertTrue(list((self.work / '.runtime' / 'recovery').glob('*.zip')))

    def test_nothing_is_committed_when_no_archive_is_healthy(self):
        (self.work / 'data/fc27/common-gen5' / ALPHA / 'matches' / '9001.json').unlink()
        head = p.git(self.work, 'rev-parse', 'HEAD').stdout
        (self.work / 'data/fc27/common-gen5' / BETA).mkdir()
        (self.work / 'data/fc27/common-gen5' / BETA / 'integrity.json').write_text('not an inventory')
        with self.assertRaisesRegex(a.ArchiveError, 'Archive check failed for club 100'):
            p.persist(self.work, 'main', pause=lambda _: None)
        self.assertEqual(p.git(self.work, 'rev-parse', 'HEAD').stdout, head)


if __name__ == '__main__':
    unittest.main()
