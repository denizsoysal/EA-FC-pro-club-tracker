"""Fault injection and byte-preservation tests. No live EA/GitHub access."""
import base64
import copy
import io
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
import zipfile

from test_tracker import t, config, match
import archive_store as a


class Response(io.BytesIO):
    status = 200
    headers = {"Content-Type": "application/json"}
    def __enter__(self): return self
    def __exit__(self, *_): self.close()


class ArchiveSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cfg = config()
        t.write_json(self.root / 'config.json', self.cfg)
        self.store = a.ArchiveStore(t.archive_dir(self.root, self.cfg), self.cfg)

    def tearDown(self): self.temp.cleanup()
    def save(self, payload): return t.save_matches(self.root, self.cfg, 'leagueMatch', payload)
    def path(self): return self.store.folder / 'matches' / '900.json'
    def versions(self): return [a.load(p)['raw'] for p in (self.store.folder / 'revisions' / '900').glob('*.json')]

    def test_all_raw_fields_and_both_teams_preserved(self):
        raw = match()
        raw['unrecognised_future_field'] = {'huge_id': 1234567890123456789012345, 'unicode': 'é ⚽', 'null': None}
        raw['players']['200'] = {'opponent': {'newfield': ['anything', 6], 'match_event_aggregate_99': '9999:25'}}
        raw['players']['100']['500']['match_event_aggregate_0'] += ',36:8,999:17'
        self.save([raw])
        self.assertEqual(a.load(self.path())['raw'], raw)
        self.assertIn(raw, self.versions())
        snapshots = [a.load(p) for p in (self.store.folder / 'snapshots').rglob('*.json')]
        self.assertIn([raw], snapshots)

    def test_legacy_history_protected_without_rewriting_it(self):
        raw = match()
        record = dict(schema_version=1, edition='fc27', platform='common-gen5', club_id='100',
                      match_id='900', match_types=['leagueMatch'], first_archived_at='original',
                      last_changed_at='original', raw=raw)
        self.path().parent.mkdir(parents=True)
        original = json.dumps(record, ensure_ascii=False).encode('utf-8')
        self.path().write_bytes(original)
        report = self.store.prepare()
        self.assertEqual(self.path().read_bytes(), original)
        self.assertEqual(report['matches'], 1)
        self.assertEqual(report['revisions'], 1)
        self.assertEqual(next((self.store.folder / 'revisions').rglob('*.json')).read_bytes(), original)

    def test_identical_poll_does_not_rewrite_or_duplicate_matches(self):
        self.save([match()])
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.store.folder.rglob('*.json')}
        self.save([match()])
        after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.store.folder.rglob('*.json')}
        self.assertEqual(before, after)

    def test_revised_match_retains_original_and_latest(self):
        before = match(); self.save([before])
        revised = copy.deepcopy(before); revised['players']['100']['500']['rating'] = '9.5'
        self.save([revised])
        self.assertEqual(a.load(self.path())['raw'], revised)
        self.assertCountEqual(self.versions(), [before, revised])
        self.assertEqual(self.store.verify()['matches'], 1)

    def test_sparse_later_response_cannot_erase_original_fields_from_history(self):
        before = match(); self.save([before])
        sparse = match(); sparse['players'] = {}
        self.save([sparse])
        self.assertIn(before, self.versions())
        self.assertIn(sparse, self.versions())
        self.assertEqual(len(self.versions()), 2)

    def test_unknown_field_only_change_is_a_retained_revision(self):
        before = match(); self.save([before])
        later = copy.deepcopy(before); later['not_displayed'] = 'still keep me'
        self.save([later])
        self.assertIn(later, self.versions())

    def test_empty_and_null_responses_retained_without_deleting_old_games(self):
        self.save([match()]); before = self.path().read_bytes()
        self.save([]); self.save(None)
        self.assertEqual(self.path().read_bytes(), before)
        self.assertEqual(self.store.verify()['snapshots'], 3)

    def test_invalid_response_kept_but_not_admitted_to_matches(self):
        payload = {'unexpected': 'API error in a JSON object'}
        with self.assertRaises(ValueError): self.save(payload)
        self.assertEqual(self.store.verify()['matches'], 0)
        self.assertIn(payload, [a.load(p) for p in (self.store.folder / 'snapshots').rglob('*.json')])

    def test_mixed_invalid_feed_kept_in_full_before_validation(self):
        payload = [match(), {'matchId': 'bad', 'unseen_data': [1, 2, 3]}]
        with self.assertRaises(ValueError): self.save(payload)
        self.assertEqual(self.store.verify()['matches'], 0)
        self.assertIn(payload, [a.load(p) for p in (self.store.folder / 'snapshots').rglob('*.json')])

    def test_checksum_detects_valid_json_edit(self):
        self.save([match()]); data = a.load(self.path()); data['raw']['extra'] = 'tampered'
        self.path().write_bytes(a.encoded(data))
        with self.assertRaisesRegex(a.ArchiveError, 'Checksum'): self.store.verify()

    def test_deleted_match_detected(self):
        self.save([match()]); self.path().unlink()
        with self.assertRaisesRegex(a.ArchiveError, 'Missing archived'): self.store.verify()

    def test_deleted_revision_detected(self):
        self.save([match()]); next((self.store.folder / 'revisions').rglob('*.json')).unlink()
        with self.assertRaisesRegex(a.ArchiveError, 'Missing archived'): self.store.verify()

    def test_deleted_inventory_is_not_silently_recreated(self):
        self.save([match()]); self.store.inventory.unlink()
        with self.assertRaisesRegex(a.ArchiveError, 'inventory is missing'): self.store.prepare()

    def test_new_untracked_file_is_not_silently_blessed(self):
        self.save([match()]); (self.path().parent / '999.json').write_text('{}')
        with self.assertRaisesRegex(a.ArchiveError, 'Untracked'): self.store.verify()

    def test_corrupt_archive_stops_before_requesting_ea(self):
        self.save([match()]); self.path().write_text('broken')
        fetch = Mock()
        report = t.collect(self.root, self.cfg, fetch=fetch, pause=lambda _: None)
        self.assertTrue(report['needs_attention']); fetch.assert_not_called()
        self.assertEqual(self.path().read_text(), 'broken')

    def test_atomic_fsync_failure_preserves_previous_file(self):
        file = self.root / 'ordinary.json'; file.write_bytes(b'old')
        with patch.object(a.os, 'fsync', side_effect=OSError('simulated disk full')):
            with self.assertRaises(OSError): a.atomic_bytes(file, b'new')
        self.assertEqual(file.read_bytes(), b'old')

    def test_interrupted_match_replace_is_recovered_from_journal(self):
        before = match(); self.save([before])
        later = copy.deepcopy(before); later['players']['100']['500']['rating'] = '9.1'
        replace = a.os.replace
        def fail_match(src, dst):
            if Path(dst) == self.path(): raise OSError('simulated interrupted match replace')
            return replace(src, dst)
        with patch.object(a.os, 'replace', side_effect=fail_match):
            with self.assertRaises(OSError): self.save([later])
        self.assertTrue(self.store.journal.exists())
        self.assertEqual(a.load(self.path())['raw'], before)
        self.store.prepare()
        self.assertEqual(a.load(self.path())['raw'], later)
        self.assertCountEqual(self.versions(), [before, later])
        self.assertFalse(self.store.journal.exists())

    def test_interruption_after_target_write_before_inventory_is_recoverable(self):
        before = match(); self.save([before])
        later = copy.deepcopy(before); later['goalsUpdated'] = True
        write = a.atomic_bytes
        def fail_inventory(path, body):
            if path == self.store.inventory and a.load(self.path())['raw'] == later:
                raise OSError('simulated loss of process before inventory commit')
            return write(path, body)
        with patch.object(a, 'atomic_bytes', side_effect=fail_inventory):
            with self.assertRaises(OSError): self.save([later])
        self.assertTrue(self.store.journal.exists())
        self.store.verify()
        self.assertEqual(a.load(self.path())['raw'], later)
        self.assertCountEqual(self.versions(), [before, later])

    def test_failure_before_journal_does_not_erase_old_match_or_response(self):
        before = match(); self.save([before])
        later = copy.deepcopy(before); later['extra'] = 'new'
        write = a.atomic_bytes
        def fail_match_journal(path, body):
            if path == self.store.journal and any(c['path'].startswith('matches/') for c in json.loads(body)['changes']):
                raise OSError('simulated journal write failure')
            return write(path, body)
        with patch.object(a, 'atomic_bytes', side_effect=fail_match_journal):
            with self.assertRaises(OSError): self.save([later])
        self.assertEqual(a.load(self.path())['raw'], before)
        self.assertIn([later], [a.load(p) for p in (self.store.folder / 'snapshots').rglob('*.json')])
        self.store.verify()

    def test_immutable_object_cannot_be_replaced(self):
        self.save([match()])
        relative = next(p for p in self.store.files() if p.startswith('snapshots/'))
        with self.assertRaises(a.ArchiveError): self.store.write_many({relative: b'[]\n'})

    def test_unsafe_storage_path_rejected(self):
        self.store.prepare()
        for relative in ['../bad.json', '/tmp/bad.json', 'snapshots/../../bad.json', 'snapshots\\bad.json']:
            with self.subTest(path=relative):
                with self.assertRaises(a.ArchiveError): self.store.write_many({relative: b'[]\n'})

    def test_process_lock_rejects_second_writer_then_releases(self):
        with a.repository_lock(self.root):
            with self.assertRaises(a.ArchiveError):
                with a.repository_lock(self.root): pass
        with a.repository_lock(self.root): pass

    def test_backup_roundtrip_all_editions_and_config(self):
        self.save([match()])
        other = copy.deepcopy(self.cfg); other['edition'] = 'fc28'
        t.save_matches(self.root, other, 'leagueMatch', [match('902')])
        info = a.create_backup(self.root)
        target = self.root / 'restored'; target.mkdir()
        with zipfile.ZipFile(info['backup']) as z: z.extractall(target)
        self.assertEqual(a.load(target / 'config.json'), self.cfg)
        report = t.archive_check(target, self.cfg)
        self.assertEqual(len(report['archives']), 2)
        self.assertTrue(all(x['matches'] == 1 for x in report['archives']))
        self.assertEqual(a.verify_backup(Path(info['backup']))['status'], 'ok')

    def test_backup_refuses_overwrite(self):
        self.save([match()]); path = self.root / 'backup.zip'
        a.create_backup(self.root, path); before = path.read_bytes()
        with self.assertRaises(a.ArchiveError): a.create_backup(self.root, path)
        self.assertEqual(path.read_bytes(), before)

    def test_backup_checks_content_not_only_zip_crc(self):
        self.save([match()]); info = a.create_backup(self.root)
        bad = self.root / 'bad.zip'
        with zipfile.ZipFile(info['backup']) as src, zipfile.ZipFile(bad, 'w') as dst:
            for name in src.namelist():
                dst.writestr(name, b'{}' if name == 'config.json' else src.read(name))
        with self.assertRaisesRegex(a.ArchiveError, 'checksum'): a.verify_backup(bad)

    def test_backup_cannot_be_saved_inside_archive(self):
        self.save([match()])
        with self.assertRaises(a.ArchiveError): a.create_backup(self.root, self.store.folder / 'backup.zip')

    def test_raw_http_response_bytes_preserved_before_json_decode(self):
        self.store.prepare()
        body = b' [ {"nested": [1, 2], "exact_whitespace": true} ] '
        sink = lambda status, raw, ct, tr: self.store.http_response('leagueMatch', status, raw, ct, tr)
        with patch.object(t, 'urlopen', return_value=Response(body)):
            self.assertEqual(t.get_json('clubs/matches', {}, raw_sink=sink), json.loads(body))
        response = a.load(next((self.store.folder / 'responses').rglob('*.json')))
        self.assertEqual(base64.b64decode(response['body_base64']), body)
        self.assertFalse(response['truncated'])

    def test_html_response_kept_even_when_json_parsing_fails(self):
        self.store.prepare(); body = b'<html>Temporary error</html>'
        sink = lambda status, raw, ct, tr: self.store.http_response('leagueMatch', status, raw, ct, tr)
        with patch.object(t, 'urlopen', return_value=Response(body)):
            with self.assertRaises(t.EAError): t.get_json('clubs/matches', {}, raw_sink=sink)
        response = a.load(next((self.store.folder / 'responses').rglob('*.json')))
        self.assertEqual(base64.b64decode(response['body_base64']), body)

    def test_http403_body_kept_and_no_cookies_saved(self):
        self.store.prepare(); body = b'access denied'
        exc = HTTPError('https://example.invalid', 403, 'Forbidden', {'Content-Type': 'text/plain', 'Set-Cookie': 'NEVER_SAVE'}, io.BytesIO(body))
        sink = lambda status, raw, ct, tr: self.store.http_response('leagueMatch', status, raw, ct, tr)
        with patch.object(t, 'urlopen', side_effect=exc):
            with self.assertRaises(t.EAError): t.get_json('clubs/matches', {}, raw_sink=sink)
        response_path = next((self.store.folder / 'responses').rglob('*.json'))
        response = a.load(response_path)
        self.assertEqual(response['status'], 403)
        self.assertEqual(base64.b64decode(response['body_base64']), body)
        self.assertNotIn('NEVER_SAVE', response_path.read_text())

    def test_oversized_response_is_explicitly_flagged_not_silently_truncated(self):
        self.store.prepare(); sink = lambda status, raw, ct, tr: self.store.http_response('leagueMatch', status, raw, ct, tr)
        with patch.object(t, 'MAX_RESPONSE', 32), patch.object(t, 'urlopen', return_value=Response(b'X' * 40)):
            with self.assertRaisesRegex(t.EAError, 'size limit'): t.get_json('clubs/matches', {}, raw_sink=sink)
        response = a.load(next((self.store.folder / 'responses').rglob('*.json')))
        self.assertTrue(response['truncated'])
        self.assertEqual(len(base64.b64decode(response['body_base64'])), 32)

    def test_parser_error_does_not_mutate_raw_history(self):
        raw = match(); raw['players']['100']['500']['match_event_aggregate_0'] = 'future,unknown,format'
        self.save([raw]); before = self.path().read_bytes()
        view = t.build(self.root, self.cfg)
        self.assertIsNone(view['matches'][0]['players'][0]['dribbles_completed'])
        self.assertEqual(self.path().read_bytes(), before)

    def test_build_failure_does_not_delete_already_saved_data(self):
        self.save([match()]); before = self.path().read_bytes()
        with patch.object(t, 'normalize_match', side_effect=ValueError('simulated viewer bug')):
            with self.assertRaises(ValueError): t.build(self.root, self.cfg)
        self.assertEqual(self.path().read_bytes(), before)
        self.store.verify()


    def crash_child(self, after_replace: bool):
        before = match(); self.save([before])
        later = copy.deepcopy(before); later['after_crash_test'] = 'preserve this too'
        input_path = self.root / 'incoming.json'; input_path.write_bytes(a.encoded([later]))
        script = r"""
import sys, json, os
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import tracker as t
import archive_store as a
root = Path(sys.argv[2]); cfg = t.load_config(root)
path = t.archive_dir(root, cfg) / 'matches' / '900.json'
real_replace = os.replace
after = sys.argv[3] == 'after'
def crash_replace(src, dst):
    if Path(dst) == path:
        if after: real_replace(src, dst)
        os._exit(73)
    return real_replace(src, dst)
a.os.replace = crash_replace
with a.repository_lock(root):
    t.save_matches(root, cfg, 'leagueMatch', json.loads((root / 'incoming.json').read_bytes()))
"""
        result = subprocess.run([sys.executable, '-c', script,
                                 str(Path(t.__file__).parent), str(self.root),
                                 'after' if after_replace else 'before'], capture_output=True)
        self.assertEqual(result.returncode, 73, result.stderr.decode(errors='replace'))
        self.assertTrue(self.store.journal.exists())
        # No stale lock remains after real process termination.
        with a.repository_lock(self.root): self.store.prepare()
        self.assertEqual(a.load(self.path())['raw'], later)
        self.assertCountEqual(self.versions(), [before, later])
        self.store.verify()

    def test_actual_process_exit_before_match_replace_recovers(self):
        self.crash_child(False)

    def test_actual_process_exit_after_match_replace_recovers(self):
        self.crash_child(True)


if __name__ == '__main__': unittest.main()
