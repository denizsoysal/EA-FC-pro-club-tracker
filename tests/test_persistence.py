"""Integration tests against a LOCAL bare Git remote, not the GitHub service."""
import copy
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_tracker import t, config, match
import archive_store as a
import persist_archive as p


@unittest.skipUnless(shutil.which('git'), 'Git is required for local persistence integration tests')
class GitPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.remote = self.base / 'remote.git'
        self.work = self.base / 'work'; self.work.mkdir()
        subprocess.run(['git', 'init', '--bare', str(self.remote)], check=True, capture_output=True)
        p.git(self.work, 'init', '-b', 'main')
        self.cfg = config()
        t.write_json(self.work / 'config.json', self.cfg)
        (self.work / '.gitignore').write_text('.runtime/\nsite/data/\nbackups/\n')
        shutil.copyfile(Path(__file__).resolve().parents[1] / '.gitattributes', self.work / '.gitattributes')
        (self.work / 'README.md').write_text('Initial version')
        t.save_matches(self.work, self.cfg, 'leagueMatch', [match()])
        p.git(self.work, 'add', '.')
        self.commit(self.work, 'Initial archive')
        p.git(self.work, 'remote', 'add', 'origin', str(self.remote))
        p.git(self.work, 'push', '-u', 'origin', 'main')

    def tearDown(self): self.tmp.cleanup()
    def commit(self, folder, message):
        return p.git(folder, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', message)
    def clone(self, name):
        folder = self.base / name
        subprocess.run(['git', 'clone', '--branch', 'main', str(self.remote), str(folder)], check=True, capture_output=True)
        p.git(folder, 'config', 'user.name', 'Test')
        p.git(folder, 'config', 'user.email', 'test@example.invalid')
        return folder
    def add_local_match(self): t.save_matches(self.work, self.cfg, 'leagueMatch', [match('901')])

    def test_raw_matches_and_revisions_reach_remote_before_deployment(self):
        self.add_local_match()
        result = p.persist(self.work, 'main', pause=lambda _: None)
        self.assertEqual(result['status'], 'pushed')
        self.assertEqual(a.verify_backup(Path(result['recovery_zip']))['status'], 'ok')
        clone = self.clone('check')
        report = t.archive_check(clone, self.cfg)['archives'][0]
        self.assertEqual(report['matches'], 2)
        self.assertEqual(report['revisions'], 2)

    def test_identical_poll_does_not_create_empty_git_commit(self):
        before = p.git(self.work, 'rev-parse', 'HEAD').stdout.strip()
        result = p.persist(self.work, 'main', pause=lambda _: None)
        self.assertFalse(result['new_commit'])
        self.assertEqual(p.git(self.work, 'rev-parse', 'HEAD').stdout.strip(), before)

    def test_unrelated_concurrent_readme_commit_rebases_without_data_loss(self):
        other = self.clone('other')
        (other / 'README.md').write_text('Concurrent user edit')
        p.git(other, 'add', 'README.md'); self.commit(other, 'User edit'); p.git(other, 'push')
        self.add_local_match()
        p.persist(self.work, 'main', pause=lambda _: None)
        final = self.clone('final')
        self.assertEqual((final / 'README.md').read_text(), 'Concurrent user edit')
        self.assertEqual(t.archive_check(final, self.cfg)['archives'][0]['matches'], 2)

    def test_conflicting_archive_updates_abort_instead_of_force_push(self):
        other = self.clone('other')
        remote_match = match(); remote_match['players']['100']['500']['rating'] = '8.8'
        t.save_matches(other, self.cfg, 'leagueMatch', [remote_match])
        p.git(other, 'add', 'data'); self.commit(other, 'Other archive edit'); p.git(other, 'push')
        local_match = match(); local_match['players']['100']['500']['rating'] = '9.9'
        t.save_matches(self.work, self.cfg, 'leagueMatch', [local_match])
        p.git(self.work, 'config', 'user.name', 'Test'); p.git(self.work, 'config', 'user.email', 'test@example.invalid')
        with self.assertRaisesRegex(a.ArchiveError, 'No force-push'): p.persist(self.work, 'main', pause=lambda _: None)
        raw = a.load(t.archive_dir(self.work, self.cfg) / 'matches' / '900.json')['raw']
        self.assertEqual(raw, local_match)
        final = self.clone('final')
        self.assertEqual(a.load(t.archive_dir(final, self.cfg) / 'matches' / '900.json')['raw'], remote_match)
        self.assertTrue(list((self.work / '.runtime' / 'recovery').glob('*.zip')))

    def test_denied_push_keeps_verified_recovery_zip(self):
        self.add_local_match()
        real = p.git
        def denied(root, *args, **kwargs):
            if args[0] == 'push': return subprocess.CompletedProcess(['git', *args], 1, '', 'Simulated remote denial')
            return real(root, *args, **kwargs)
        with patch.object(p, 'git', side_effect=denied):
            with self.assertRaisesRegex(a.ArchiveError, 'Git push failed'): p.persist(self.work, 'main', pause=lambda _: None)
        backup = next((self.work / '.runtime' / 'recovery').glob('*.zip'))
        self.assertEqual(a.verify_backup(backup)['status'], 'ok')
        self.assertEqual(t.archive_check(self.work, self.cfg)['archives'][0]['matches'], 2)
        self.assertEqual(t.archive_check(self.clone('final'), self.cfg)['archives'][0]['matches'], 1)

    def test_deleted_archive_is_not_committed_as_success(self):
        path = t.archive_dir(self.work, self.cfg) / 'matches' / '900.json'
        path.unlink()
        with self.assertRaises(a.ArchiveError): p.persist(self.work, 'main', pause=lambda _: None)
        final = self.clone('final')
        self.assertTrue((t.archive_dir(final, self.cfg) / 'matches' / '900.json').exists())
        self.assertTrue(list((self.work / '.runtime/recovery').glob('*.zip')))

    def test_viewer_failure_does_not_prevent_independent_raw_persistence(self):
        self.add_local_match()
        with patch.object(t, 'normalize_match', side_effect=ValueError('Simulated broken viewer')):
            with self.assertRaises(ValueError): t.build(self.work, self.cfg)
        p.persist(self.work, 'main', pause=lambda _: None)
        self.assertEqual(t.archive_check(self.clone('final'), self.cfg)['archives'][0]['matches'], 2)

    def test_workflow_saves_after_failed_fetch_build_and_keeps_cancellation_off(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/archive-and-publish.yml').read_text()
        self.assertIn('cancel-in-progress: false', workflow)
        self.assertIn("steps.fetch.outcome == 'failure'", workflow)
        self.assertIn("steps.persist.outcome == 'failure'", workflow)
        self.assertIn('actions/upload-artifact@', workflow)
        self.assertIn('retention-days: 14', workflow)
        self.assertLess(workflow.index('scripts/persist_archive.py'), workflow.index('actions/deploy-pages'))
        self.assertNotIn('--force', workflow)


    def test_windows_style_legacy_bytes_survive_git_roundtrip_with_autocrlf(self):
        # Linux Git can exercise the same CRLF conversion setting without a
        # Windows OS. This is not a test of Windows filesystem/locking APIs.
        p.git(self.work, 'config', 'core.autocrlf', 'true')
        before = match('905')
        folder = t.archive_dir(self.work, self.cfg)
        # Use a separate legacy edition so its checksum baseline has not been set.
        legacy = copy.deepcopy(self.cfg); legacy['edition'] = 'fc26'
        file = t.archive_dir(self.work, legacy) / 'matches' / '905.json'
        record = dict(schema_version=1, edition='fc26', platform='common-gen5', club_id='100',
                      match_id='905', match_types=['leagueMatch'], first_archived_at='original',
                      last_changed_at='original', raw=before)
        content = a.encoded(record).replace(b'\n', b'\r\n')
        file.parent.mkdir(parents=True); file.write_bytes(content)
        p.persist(self.work, 'main', pause=lambda _: None)
        clone = self.clone('windows-style-clone')
        copied = t.archive_dir(clone, legacy) / 'matches' / '905.json'
        self.assertEqual(copied.read_bytes(), content)
        self.assertTrue(all(r['status'] == 'ok' for r in t.archive_check(clone, self.cfg)['archives']))

    def test_git_byte_transformation_is_detected_before_commit(self):
        p.git(self.work, 'config', 'core.autocrlf', 'true')
        # Remove the override to reproduce the original project's dangerous rule.
        (self.work / '.gitattributes').write_text('*.json text eol=lf\n')
        file = self.work / 'data' / 'line-endings.json'; file.write_bytes(b'{\r\n  "test": 1\r\n}\r\n')
        original_head = p.git(self.work, 'rev-parse', 'HEAD').stdout
        with self.assertRaisesRegex(a.ArchiveError, 'Git would change archived bytes'):
            p.persist(self.work, 'main', pause=lambda _: None)
        self.assertEqual(p.git(self.work, 'rev-parse', 'HEAD').stdout, original_head)
        self.assertTrue(list((self.work / '.runtime/recovery').glob('*.zip')))


if __name__ == '__main__': unittest.main()
