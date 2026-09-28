"""Offline tests with synthetic records, NOT proof of EA FC27 connectivity."""
import copy
import csv
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

SPEC = importlib.util.spec_from_file_location('tracker', Path(__file__).resolve().parents[1] / 'scripts/tracker.py')
t = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(t)


def config():
    return {'club_name': 'Synthetic Club', 'club_id': '100', 'platform': 'common-gen5',
            'edition': 'fc27', 'match_types': ['leagueMatch', 'playoffMatch'],
            'collection_enabled': True, 'advanced_mapping_confirmed': False}


def player(name='Synthetic Player'):
    return {'playername': name, 'goals': '2', 'assists': '1', 'rating': '8.2',
            'passesmade': '20', 'passattempts': '25',
            'match_event_aggregate_0': '11:1,115:2,174:9',
            'match_event_aggregate_1': '', 'match_event_aggregate_2': '',
            'match_event_aggregate_3': ''}


def match(mid='900'):
    return {'matchId': mid, 'timestamp': '1790596800',
            'clubs': {'100': {'goals': '3', 'goalsAgainst': '1', 'wins': '1',
                               'details': {'name': 'Synthetic Club'}},
                      '200': {'goals': '1', 'details': {'name': 'Synthetic Opponent'}}},
            'players': {'100': {'500': player()}, '200': {}}}


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cfg = config()
        t.write_json(self.root / 'config.json', self.cfg)
    def tearDown(self):
        self.temp.cleanup()
    def save(self, data, kind='leagueMatch'):
        return t.save_matches(self.root, self.cfg, kind, data)
    def records(self):
        return list((t.archive_dir(self.root, self.cfg) / 'matches').glob('*.json'))
    def test_decode(self):
        events, status=t.decode_events(player())
        self.assertEqual(events[115],2);self.assertEqual(events[174],9)
    def test_sparse_zero(self):
        p=player();p[t.BUCKETS[0]]='11:1'
        self.assertEqual(t.normalize_player('500',p)['second_assists'],0)
    def test_missing_not_zero(self):
        p=player();del p[t.BUCKETS[1]]
        self.assertIsNone(t.normalize_player('500',p)['second_assists'])
    def test_empty_not_zero(self):
        p=player()
        for key in t.BUCKETS:p[key]=''
        self.assertIsNone(t.decode_events(p)[0])
    def test_malformed(self):
        p=player();p[t.BUCKETS[1]]='not-an-event'
        self.assertIsNone(t.decode_events(p)[0])
    def test_duplicate_event(self):
        p=player();p[t.BUCKETS[1]]='115:2'
        self.assertIsNone(t.decode_events(p)[0])
    def test_idempotence(self):
        self.assertEqual(self.save([match()])['changed'],1)
        before=self.records()[0].read_bytes()
        self.assertEqual(self.save([match()])['changed'],0)
        self.assertEqual(self.records()[0].read_bytes(),before)
    def test_empty_feed_does_not_delete(self):
        self.save([match()]);self.save([]);self.save(None)
        self.assertEqual(len(self.records()),1)
    def test_revised_match_updates_not_duplicates(self):
        self.save([match()]);m=match();m['players']['100']['500']['rating']='8.7'
        self.save([m]);self.assertEqual(len(self.records()),1)
        self.assertEqual(t.read_json(self.records()[0])['raw'],m)
    def test_same_match_in_two_types_not_double_counted(self):
        self.save([match()]);self.save([match()],'playoffMatch')
        self.assertEqual(len(self.records()),1)
        self.assertEqual(len(t.build(self.root,self.cfg)['matches']),1)
    def test_edition_isolated(self):
        self.save([match()]);cfg=copy.deepcopy(self.cfg);cfg['edition']='fc28'
        self.assertEqual(t.build(self.root,cfg)['matches'],[])
    def test_wrong_club_rejects_whole_feed(self):
        bad=match('901');del bad['clubs']['100']
        with self.assertRaises(ValueError):self.save([match(),bad])
        self.assertEqual(len(self.records()),0)
    def test_unsafe_id_rejected(self):
        with self.assertRaises(ValueError):self.save([match('../unsafe')])
    def test_config_path_traversal_rejected(self):
        cfg=config();cfg['edition']='../../etc';t.write_json(self.root/'config.json',cfg)
        with self.assertRaises(ValueError):t.load_config(self.root)
    def test_numeric_missing(self):
        for value in ('',None,'NaN','Infinity',-1,False):self.assertIsNone(t.number(value))
        self.assertEqual(t.number('0'),0)
    def test_player_name_field_and_assist_check(self):
        p=player('Exact EA playername');p['assists']='3'
        row=t.normalize_player('500',p)
        self.assertEqual(row['name'],'Exact EA playername')
        self.assertTrue(row['assists_counter_disagrees'])
    def test_unknown_score_not_draw(self):
        m=match();m['clubs']['100']={};m['clubs']['200']={}
        self.save([m]);row=t.build(self.root,self.cfg)['matches'][0]
        self.assertIsNone(row['result']);self.assertIsNone(row['goals'])
    def test_csv_injection_neutralized(self):
        m=match();m['players']['100']['500']['playername']='=1+1';self.save([m])
        path=t.export_csv(self.root,t.build(self.root,self.cfg))
        with path.open(encoding='utf-8-sig',newline='') as h:rows=list(csv.DictReader(h))
        self.assertEqual(rows[0]['player_name'],"'=1+1")
    def test_success_fetch(self):
        fetch=Mock(side_effect=[[match()],[]])
        report=t.collect(self.root,self.cfg,fetch=fetch,pause=lambda _:None)
        self.assertEqual(report['status'],'success');self.assertEqual(fetch.call_count,2)
    def test_access_denial_persists_pause(self):
        fetch=Mock(side_effect=t.EAError('Denied',code=403))
        report=t.collect(self.root,self.cfg,fetch=fetch,pause=lambda _:None)
        self.assertTrue(report['needs_attention']);self.assertEqual(fetch.call_count,1)
        fetch.reset_mock()
        report=t.collect(self.root,self.cfg,fetch=fetch,pause=lambda _:None)
        self.assertEqual(report['status'],'blocked');fetch.assert_not_called()
    def test_partial_success_is_archived(self):
        fetch=Mock(side_effect=[[match()],t.EAError('Denied',code=403)])
        report=t.collect(self.root,self.cfg,fetch=fetch,pause=lambda _:None)
        self.assertEqual(report['status'],'partial');self.assertEqual(len(self.records()),1)
    def test_429_cooldown_even_with_manual_resume(self):
        fetch=Mock(side_effect=t.EAError('Rate limited',code=429,retry_after='3600'))
        t.collect(self.root,self.cfg,fetch=fetch,pause=lambda _:None);fetch.reset_mock()
        report=t.collect(self.root,self.cfg,resume=True,fetch=fetch,pause=lambda _:None)
        self.assertEqual(report['status'],'cooldown');fetch.assert_not_called()
    def test_schedule_disabled(self):
        self.cfg['collection_enabled']=False;fetch=Mock()
        report=t.collect(self.root,self.cfg,event='schedule',fetch=fetch)
        self.assertEqual(report['status'],'disabled');fetch.assert_not_called()
    def test_manual_probe_works_before_schedule_enabled(self):
        self.cfg['collection_enabled']=False;fetch=Mock(return_value=[])
        self.assertEqual(t.collect(self.root,self.cfg,fetch=fetch,pause=lambda _:None)['status'],'success')
    def test_push_build_does_not_fetch(self):
        fetch=Mock();t.collect(self.root,self.cfg,event='push',fetch=fetch);fetch.assert_not_called()
    def test_gap_warning(self):
        self.save([match('1')])
        result=self.save([match(str(i)) for i in range(2,12)])
        self.assertTrue(result['possible_gap'])
    def test_first_other_competition_is_not_gap_warning(self):
        self.save([match('1')])
        result=self.save([match(str(i)) for i in range(2,12)],'playoffMatch')
        self.assertFalse(result['possible_gap'])
    def test_sqlite_migration(self):
        file=self.root/'old.sqlite3';db=sqlite3.connect(file)
        db.execute('CREATE TABLE matches (edition,platform,club_id,match_type,raw_json)')
        db.execute('INSERT INTO matches VALUES (?,?,?,?,?)',('fc27','common-gen5','100','leagueMatch',json.dumps(match())))
        db.commit();db.close()
        self.assertEqual(t.migrate_sqlite(self.root,self.cfg,file),1)
        self.assertEqual(t.migrate_sqlite(self.root,self.cfg,file),0)

if __name__=='__main__':unittest.main()
