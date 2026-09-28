"""Selected v2 metrics: synthetic data only, not semantic/live FC27 verification."""
import copy
import csv
import tempfile
import unittest
from pathlib import Path
from test_tracker import t, config, player, match


def with_events(events):
    raw = player()
    items = list(events.items())
    for i, key in enumerate(t.BUCKETS):
        raw[key] = ','.join(f'{k}:{v}' for k, v in items[i::4])
    return raw


class AdvancedStatsTests(unittest.TestCase):
    def test_every_direct_mapping(self):
        raw = with_events({event: i + 2 for i, event in enumerate(t.EVENTS.values())})
        normalized = t.normalize_player('500', raw)
        for i, field in enumerate(t.EVENTS):
            with self.subTest(field=field):
                self.assertEqual(normalized[field], i + 2)

    def test_dribble_categories_are_separate(self):
        row = t.normalize_player('500', with_events({11: 1, 112: 10, 38: 4, 174: 22}))
        self.assertEqual(row['dribble_beats'], 6)
        self.assertEqual(row['skill_move_beats'], 4)
        self.assertEqual(row['dribbles_completed'], 22)

    def test_negative_difference_is_not_clamped_to_zero(self):
        row = t.normalize_player('500', with_events({112: 2, 38: 5}))
        self.assertIsNone(row['dribble_beats'])
        self.assertEqual(row['skill_move_beats'], 5)
        self.assertIn('dribble_total_below_skill_beats', row['data_warnings'])

    def test_negative_difference_does_not_destroy_unrelated_counts(self):
        row = t.normalize_player('500', with_events({112: 2, 38: 5, 115: 3}))
        self.assertEqual(row['second_assists'], 3)

    def test_direction_and_length_rates(self):
        row = t.normalize_player('500', with_events({30: 12, 31: 3, 28: 3, 29: 2}))
        self.assertEqual(row['forward_pass_attempts'], 15)
        self.assertEqual(row['forward_pass_rate'], 80)
        self.assertEqual(row['long_pass_attempts'], 5)
        self.assertEqual(row['long_pass_rate'], 60)

    def test_successful_through_balls(self):
        self.assertEqual(t.normalize_player('500', with_events({152: 7}))['through_balls_completed'], 7)

    def test_tackles_total_is_sum_of_won_components(self):
        raw = with_events({229: 5, 230: 2, 6: 4, 158: 3})
        raw['tacklesmade'] = '999'
        row = t.normalize_player('500', raw)
        self.assertEqual(row['tackles_won'], 7)
        self.assertEqual(row['interceptions'], 4)
        self.assertEqual(row['opponents_dispossessed'], 3)
        self.assertEqual(row['tackles_made'], 999)

    def test_shots_are_not_reconstructed_from_target_categories(self):
        row = t.normalize_player('500', with_events({217: 3, 218: 2}))
        self.assertIsNone(row['shots'])
        self.assertEqual(row['shots_on_target'], 3)

    def test_ordinary_shots_kept(self):
        raw = with_events({217: 3, 218: 2}); raw['shots'] = '8'
        row = t.normalize_player('500', raw)
        self.assertEqual(row['shots'], 8)
        self.assertEqual(row['shots_on_target'], 3)

    def test_all_advanced_counts_missing_when_buckets_missing(self):
        raw = player(); del raw[t.BUCKETS[2]]
        row = t.normalize_player('500', raw)
        for key in [*t.EVENTS, 'dribble_beats', 'tackles_won', 'forward_pass_rate', 'long_pass_rate']:
            with self.subTest(key=key): self.assertIsNone(row[key])
        self.assertEqual(row['pass_rate'], 80)
        self.assertEqual(row['assists'], 1)

    def test_sparse_valid_maps_produce_count_zeros_not_rate_zeros(self):
        row = t.normalize_player('500', with_events({11: 1}))
        self.assertEqual(row['forward_passes_completed'], 0)
        self.assertEqual(row['forward_pass_attempts'], 0)
        self.assertEqual(row['dribble_beats'], 0)
        self.assertIsNone(row['forward_pass_rate'])
        self.assertIsNone(row['long_pass_rate'])

    def test_valid_zero_percent_is_kept(self):
        row = t.normalize_player('500', with_events({31: 3, 29: 4}))
        self.assertEqual(row['forward_pass_rate'], 0)
        self.assertEqual(row['long_pass_rate'], 0)

    def test_invalid_ordinary_pass_counts_are_flagged(self):
        raw = player(); raw.update(passesmade='25', passattempts='20')
        row = t.normalize_player('500', raw)
        self.assertIsNone(row['pass_rate'])
        self.assertIn('completed_passes_exceed_attempts', row['data_warnings'])

    def test_invalid_alias_does_not_hide_valid_alternative(self):
        raw = player(); raw.update(passesmade='', passesMade='15')
        self.assertEqual(t.normalize_player('500', raw)['passes_made'], 15)

    def test_no_key_pass_or_pass_bypass_guess(self):
        row = t.normalize_player('500', with_events({115: 3, 152: 5}))
        self.assertNotIn('key_passes', row)
        self.assertNotIn('players_beaten_by_pass', row)

    def test_crosses_not_in_normalized_columns(self):
        row = t.normalize_player('500', with_events({36: 4, 37: 2, 157: 6, 11: 1}))
        self.assertFalse(any('cross' in key for key in row))
        self.assertFalse(any('cross' in key for key in t.CSV_STATS))

    def test_rate_helper_edge_cases(self):
        for completed, attempted in [(None, 10), (3, None), (0, 0), (12, 10), (-1, 2), (False, 5)]:
            with self.subTest(pair=(completed, attempted)):
                self.assertIsNone(t.completion_rate(completed, attempted))
        self.assertEqual(t.completion_rate(0, 2), 0)

    def test_legacy_mapping_approval_does_not_approve_v2(self):
        cfg = config(); cfg['advanced_mapping_confirmed'] = True
        self.assertFalse(t.mapping_reviewed(cfg))
        cfg['advanced_mapping_version'] = 'community-115-174-v1-unofficial'
        self.assertFalse(t.mapping_reviewed(cfg))
        cfg['advanced_mapping_version'] = t.DECODER
        self.assertTrue(t.mapping_reviewed(cfg))
        cfg['advanced_mapping_confirmed'] = False
        self.assertFalse(t.mapping_reviewed(cfg))

    def test_optional_mapping_version_config_is_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); cfg = config()
            t.write_json(root / 'config.json', cfg)
            self.assertEqual(t.load_config(root), cfg)
            cfg['advanced_mapping_version'] = 12
            t.write_json(root / 'config.json', cfg)
            with self.assertRaises(ValueError): t.load_config(root)

    def test_rebuild_does_not_modify_raw_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); cfg = config(); raw = match()
            raw['players']['100']['500'] = with_events({11: 1, 152: 5, 112: 8, 38: 2, 36: 4, 999: 12})
            t.save_matches(root, cfg, 'leagueMatch', [raw])
            path = next((t.archive_dir(root, cfg) / 'matches').glob('*.json'))
            before = path.read_bytes(); payload = t.build(root, cfg)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(t.read_json(path)['raw'], raw)
            self.assertEqual(payload['schema_version'], 2)
            self.assertEqual(payload['matches'][0]['players'][0]['dribble_beats'], 6)
            self.assertEqual(payload['matches'][0]['players'][0]['through_balls_completed'], 5)

    def test_export_contains_selected_metrics_coverage_and_no_crosses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); cfg = config(); raw = match()
            raw['players']['100']['500'] = with_events({11: 1, 30: 3, 31: 1, 112: 1, 38: 2})
            t.save_matches(root, cfg, 'leagueMatch', [raw])
            path = t.export_csv(root, t.build(root, cfg))
            with path.open(encoding='utf-8-sig', newline='') as handle:
                reader = csv.DictReader(handle); rows = list(reader); headers = reader.fieldnames
            self.assertTrue(set(t.CSV_STATS).issubset(headers))
            self.assertFalse(any('cross' in field for field in headers))
            self.assertEqual(rows[0]['dribble_beats'], '')
            self.assertNotIn('forward_pass_rate', headers)
            self.assertNotIn('long_pass_rate', headers)
            self.assertIn('dribble_total_below_skill_beats', rows[0]['data_warnings'])
            self.assertEqual(rows[0]['decoder'], t.DECODER)

    def test_old_ordinary_stats_work_without_event_buckets(self):
        row = t.normalize_player('500', {'playername': 'Legacy', 'goals': '2', 'shots': '5', 'rating': '8.0'})
        self.assertEqual(row['goals'], 2)
        self.assertEqual(row['shots'], 5)
        self.assertIsNone(row['shots_on_target'])


if __name__ == '__main__':
    unittest.main()
