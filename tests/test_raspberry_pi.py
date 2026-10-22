"""The Raspberry Pi collector and the publish-only workflow.

Mocked EA responses and a LOCAL bare Git remote; no EA, GitHub or real Pi.
collect.sh runs under bash here, not under systemd on Raspberry Pi OS.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
import unittest.mock

from test_tracker import t
import archive_store as a
import persist_archive as p
import multiclub_fixtures as f
from multiclub_fixtures import ALPHA, BETA

ROOT = Path(__file__).resolve().parents[1]
PI = ROOT / 'deploy' / 'raspberry-pi'

# Loaded by every python3 started from collect.sh: EA's transport is replaced
# before tracker imports it, and request pauses are skipped.
SITECUSTOMIZE = r'''
import io, os, time, urllib.request
from urllib.parse import parse_qs, urlsplit
from urllib.error import HTTPError
from pathlib import Path
replies = Path(os.environ['DUBSFC_TEST_REPLIES'])

class Response(io.BytesIO):
    status = 200
    headers = {'Content-Type': 'application/json'}

def fake_urlopen(request, timeout=25):
    parsed = urlsplit(request.full_url)
    assert parsed.hostname == 'proclubs.ea.com', request.full_url
    query = parse_qs(parsed.query)
    name = '%s_%s' % (query['clubIds'][0], query['matchType'][0])
    with open(replies / 'requests.log', 'a') as log:
        log.write(name + '\n')
    if (replies / 'deny').exists():
        raise HTTPError(request.full_url, 403, 'Forbidden', {'Content-Type': 'text/html'}, io.BytesIO(b'<H1>Access Denied</H1>'))
    reply = replies / (name + '.json')
    return Response(reply.read_bytes() if reply.exists() else b'[]')

urllib.request.urlopen = fake_urlopen
time.sleep = lambda seconds: None
'''


def find_bash():
    if os.name == 'nt':
        # Git for Windows' bash, not WSL's System32 launcher.
        git = shutil.which('git')
        for parent in (Path(git).resolve().parents if git else []):
            if (parent / 'bin' / 'bash.exe').is_file() and (parent / 'usr' / 'bin').is_dir():
                return str(parent / 'bin' / 'bash.exe')
        return None
    return shutil.which('bash')


BASH = find_bash()


class Remote(unittest.TestCase):
    """A work clone pushing to a bare remote, seeded like the real repository."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.remote = self.base / 'remote.git'
        seed = self.base / 'seed'; seed.mkdir()
        subprocess.run(['git', 'init', '--bare', '-b', 'main', str(self.remote)], check=True, capture_output=True)
        p.git(seed, 'init', '-b', 'main')
        for folder in ('scripts', 'deploy'):
            shutil.copytree(ROOT / folder, seed / folder, ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(ROOT / '.gitattributes', seed / '.gitattributes')
        (seed / '.gitignore').write_text('.runtime/\nsite/data/\nbackups/\n__pycache__/\n')
        (seed / 'site').mkdir()
        self.cfg = f.config()
        t.write_json(seed / 'config.json', self.cfg)
        f.sync(seed, self.cfg)
        p.git(seed, 'add', '.')
        p.git(seed, '-c', 'user.name=Seed', '-c', 'user.email=seed@example.invalid', 'commit', '-m', 'Seed')
        p.git(seed, 'remote', 'add', 'origin', str(self.remote))
        p.git(seed, 'push', '-u', 'origin', 'main')
        self.work = self.clone('pi')

    def tearDown(self):
        self.tmp.cleanup()

    def clone(self, name):
        folder = self.base / name
        subprocess.run(['git', 'clone', '--branch', 'main', str(self.remote), str(folder)], check=True, capture_output=True)
        return folder

    def remote_log(self, fmt='%an|%s'):
        return subprocess.run(['git', '--git-dir', str(self.remote), 'log', f'--format={fmt}', 'main'],
                              capture_output=True, text=True, check=True).stdout.split('\n')[:-1]

    def recovery(self):
        return sorted((self.work / '.runtime' / 'recovery').glob('dubsfc-*.zip'))


class PersistOptionsTests(Remote):
    def add_match(self, mid):
        new = f.fixture(mid, int(mid) % 50, (ALPHA, 'Alpha United', 1, {}), ('970', 'Other', 0, {}))
        t.save_matches(self.work, t.configured_clubs(self.cfg)[0], 'leagueMatch', [new])

    def test_commits_carry_the_given_identity(self):
        self.add_match('9100')
        result = p.persist(self.work, 'main', pause=lambda _: None, git_name='Pi collector', git_email='pi@example.invalid')
        self.assertEqual((result['status'], result['new_commit']), ('pushed', True))
        self.assertEqual(self.remote_log('%an <%ae>')[0], 'Pi collector <pi@example.invalid>')

    def test_default_identity_is_still_the_actions_bot(self):
        self.add_match('9101')
        p.persist(self.work, 'main', pause=lambda _: None)
        self.assertEqual(self.remote_log('%an')[0], 'github-actions[bot]')

    def test_old_recovery_zips_are_pruned_only_after_a_successful_push(self):
        for n in range(4):
            self.add_match(str(9110 + n))
            result = p.persist(self.work, 'main', pause=lambda _: None)  # Keeps everything by default.
            self.assertNotIn('recovery_zips_removed', result)
        self.assertEqual(len(self.recovery()), 4)
        self.add_match('9120')
        result = p.persist(self.work, 'main', pause=lambda _: None, keep_recovery=2)
        kept = self.recovery()
        self.assertEqual(len(kept), 2)
        self.assertIn(Path(result['recovery_zip']).name, [path.name for path in kept])
        self.assertEqual(len(result['recovery_zips_removed']), 3)
        self.assertEqual(a.verify_backup(kept[-1])['status'], 'ok')
        # A failed push keeps every ZIP: they may hold the only copy of new data.
        real = p.git
        def denied(root, *args, **kwargs):
            if args[0] == 'push':
                return subprocess.CompletedProcess(['git', *args], 1, '', 'Simulated remote denial')
            return real(root, *args, **kwargs)
        self.add_match('9121')
        with unittest.mock.patch.object(p, 'git', side_effect=denied):
            with self.assertRaisesRegex(a.ArchiveError, 'Git push failed'):
                p.persist(self.work, 'main', pause=lambda _: None, keep_recovery=1)
        self.assertEqual(len(self.recovery()), 3)

    def test_prune_never_removes_the_current_zip(self):
        folder = self.base / 'zips'; folder.mkdir()
        for name in ('dubsfc-1.zip', 'dubsfc-2.zip', 'dubsfc-3.zip', 'other.zip'):
            (folder / name).write_bytes(b'x')
        self.assertEqual(p.prune_recovery(folder, 1, folder / 'dubsfc-1.zip'), ['dubsfc-2.zip'])
        self.assertEqual(sorted(path.name for path in folder.iterdir()), ['dubsfc-1.zip', 'dubsfc-3.zip', 'other.zip'])


@unittest.skipUnless(BASH, 'bash is required to run the Raspberry Pi collector script')
class CollectScriptTests(Remote):
    def setUp(self):
        super().setUp()
        self.replies = self.base / 'replies'; self.replies.mkdir()
        hooks = self.base / 'hooks'; hooks.mkdir()
        (hooks / 'sitecustomize.py').write_text(SITECUSTOMIZE)
        shims = self.base / 'bin'; shims.mkdir()
        python = Path(sys.executable).as_posix()
        (shims / 'python3').write_text(f'#!/bin/sh\nexec "{python}" "$@"\n', newline='\n')
        os.chmod(shims / 'python3', 0o755)
        self.env = dict(os.environ, DUBSFC_TEST_REPLIES=str(self.replies), PYTHONPATH=str(hooks),
                        DUBSFC_KEEP_RECOVERY='2', GIT_TERMINAL_PROMPT='0')
        self.shims = shims

    def reply(self, club, kind, matches):
        (self.replies / f'{club}_{kind}.json').write_text(json.dumps(matches), encoding='utf-8')

    def requests(self):
        log = self.replies / 'requests.log'
        return log.read_text().split() if log.exists() else []

    def run_collect(self, *args, expect=0):
        # Prepend the python3 shim inside bash so Git Bash on Windows finds it first.
        shim = self.shims.as_posix() if os.name != 'nt' else '/' + self.shims.as_posix().replace(':', '', 1)
        command = f'export PATH="{shim}:$PATH"; bash deploy/raspberry-pi/collect.sh {" ".join(args)}'
        result = subprocess.run([BASH, '-c', command], cwd=self.work, env=self.env, capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def test_scheduled_run_collects_both_clubs_and_pushes_data_only(self):
        late = f.fixture('9200', 30, (BETA, 'Beta Town', 2, {'601': f.player('Beta Runner', 2)}), ('980', 'Late', 0, {}))
        self.reply(BETA, 'leagueMatch', [f.DERBY, f.BETA_WIN, late])
        output = self.run_collect()
        self.assertEqual(self.requests(), [f'{ALPHA}_leagueMatch', f'{ALPHA}_playoffMatch', f'{BETA}_leagueMatch', f'{BETA}_playoffMatch'])
        self.assertIn('[Beta Town (200)] leagueMatch: received 3, changed 1', output)
        self.assertIn("'status': 'pushed'", output)
        self.assertEqual(self.remote_log()[0], 'DubsFC Raspberry Pi collector|Archive DubsFC match data and retained revisions')
        final = self.clone('final')
        self.assertTrue((final / 'data/fc27/common-gen5/200/matches/9200.json').is_file())
        self.assertEqual(t.archive_report(final, self.cfg)['status'], 'ok')
        changed = subprocess.run(['git', '-C', str(final), 'show', '--name-only', '--format=', 'HEAD'],
                                 capture_output=True, text=True, check=True).stdout.split()
        self.assertTrue(changed and all(name.startswith('data/') for name in changed), changed)
        # A second run with the same feed changes no match and duplicates nothing.
        self.run_collect()
        self.assertEqual(sorted(path.stem for path in (self.clone('again') / 'data/fc27/common-gen5/200/matches').glob('*.json')),
                         ['9001', '9004', '9200'])
        self.assertLessEqual(len(self.recovery()), 2)

    def test_code_pushed_from_elsewhere_is_picked_up_before_collecting(self):
        other = self.clone('laptop')
        cfg = dict(self.cfg, collection_enabled=False)
        t.write_json(other / 'config.json', cfg)
        p.git(other, 'add', 'config.json')
        p.git(other, '-c', 'user.name=Laptop', '-c', 'user.email=l@example.invalid', 'commit', '-m', 'Pause collection')
        p.git(other, 'push')
        self.run_collect()
        self.assertEqual(self.requests(), [])  # The pulled config disabled scheduled collection.
        self.assertFalse(t.read_json(self.work / 'config.json')['collection_enabled'])

    def test_access_denial_pauses_and_resume_clears_it(self):
        (self.replies / 'deny').touch()
        output = self.run_collect(expect=1)
        self.assertEqual(self.requests(), [f'{ALPHA}_leagueMatch'])  # Nothing more after the 403.
        self.assertIn('Needs attention', output)
        final = self.clone('after-denial')
        self.assertTrue(t.read_json(final / 'data/collector_state.json')['halted'])
        self.run_collect(expect=1)  # Paused: no request at all.
        self.assertEqual(len(self.requests()), 1)
        (self.replies / 'deny').unlink()
        self.run_collect('--resume')
        self.assertEqual(len(self.requests()), 5)
        self.assertFalse(t.read_json(self.clone('resumed') / 'data/collector_state.json')['halted'])
        self.run_collect()
        self.assertEqual(len(self.requests()), 9)

    def test_failed_push_is_reported_and_keeps_the_data(self):
        self.reply(ALPHA, 'leagueMatch', [f.fixture('9300', 40, (ALPHA, 'Alpha United', 1, {}), ('990', 'X', 0, {}))])
        p.git(self.work, 'remote', 'set-url', 'origin', str(self.base / 'missing.git'))
        output = self.run_collect(expect=1)
        self.assertIn('push=1', output)
        self.assertTrue((self.work / 'data/fc27/common-gen5/100/matches/9300.json').is_file())
        self.assertEqual(len(self.recovery()), 1)


class PublishOnlyWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = (ROOT / '.github/workflows/archive-and-publish.yml').read_text(encoding='utf-8')

    def test_github_never_contacts_ea(self):
        self.assertNotIn('schedule:', self.workflow)
        self.assertNotIn('cron:', self.workflow)
        self.assertNotIn('--resume', self.workflow)
        syncs = re.findall(r'scripts/tracker\.py sync[^\n]*', self.workflow)
        self.assertEqual(syncs, ['scripts/tracker.py sync --event push --defer-failure'])

    def test_publishing_still_follows_every_push_and_persistence(self):
        self.assertIn('workflow_dispatch:', self.workflow)
        self.assertIn("- 'data/**'", self.workflow)
        self.assertLess(self.workflow.index('scripts/persist_archive.py'), self.workflow.index('actions/deploy-pages'))
        self.assertIn('cancel-in-progress: false', self.workflow)
        self.assertNotIn('--force', self.workflow)

    def test_ea_requests_are_unchanged_by_a_push_build(self):
        with tempfile.TemporaryDirectory() as directory:
            fetch, calls = f.fetcher()
            report = t.collect(Path(directory), f.config(), event='push', fetch=fetch)
            self.assertEqual((calls, report['needs_attention']), ([], False))


class InstallerTests(unittest.TestCase):
    @unittest.skipUnless(BASH, 'bash is required for a syntax check')
    def test_scripts_parse(self):
        for script in ('collect.sh', 'install.sh'):
            result = subprocess.run([BASH, '-n', str(PI / script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, script + result.stderr)

    def test_timer_runs_twice_an_hour_and_catches_up(self):
        installer = (PI / 'install.sh').read_text(encoding='utf-8')
        self.assertIn('OnCalendar=*-*-* *:07,37:00', installer)
        self.assertIn('Persistent=true', installer)
        self.assertIn('Type=oneshot', installer)
        self.assertIn('After=network-online.target', installer)

    def test_scripts_keep_unix_line_endings(self):
        for script in ('collect.sh', 'install.sh'):
            self.assertNotIn(b'\r\n', (PI / script).read_bytes(), script)
        self.assertIn('*.sh text eol=lf', (ROOT / '.gitattributes').read_text())


if __name__ == '__main__':
    unittest.main()
