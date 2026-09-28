"""Clean full-installation CLI checks. EA HTTP replies are synthetic and mocked."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

from test_tracker import config, match

ROOT = Path(__file__).resolve().parents[1]

# Patch the stdlib transport before tracker imports it. This executes the real
# CLI, parsing, raw-body persistence, normalization and build, without contacting EA.
MOCK_HTTP_CLI = r'''
import io, json, runpy, sys, time
import urllib.request
from urllib.parse import parse_qs, urlsplit
from pathlib import Path

class Response(io.BytesIO):
    status = 200
    headers = {'Content-Type': 'application/json'}

def fake_urlopen(request, timeout=25):
    parsed = urlsplit(request.full_url)
    assert parsed.hostname == 'proclubs.ea.com', request.full_url
    assert parsed.path.endswith('/clubs/matches'), request.full_url
    kind = parse_qs(parsed.query)['matchType'][0]
    if kind == 'leagueMatch':
        body = Path('synthetic_http_reply.json').read_bytes()
    else:
        assert kind == 'playoffMatch', kind
        body = b'[]'
    return Response(body)

urllib.request.urlopen = fake_urlopen
time.sleep = lambda seconds: None
sys.argv = ['scripts/tracker.py', 'sync']
runpy.run_path('scripts/tracker.py', run_name='__main__')
'''


class CompleteInstallTests(unittest.TestCase):
    def test_fresh_cli_sync_backup_verify_and_repeated_sync(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for folder in ('scripts', 'site'):
                shutil.copytree(ROOT / folder, root / folder,
                                ignore=shutil.ignore_patterns('__pycache__', 'data'))
            self.assertFalse((root / 'data').exists())
            self.assertFalse((root / 'config.json').exists())
            cfg = config()
            cfg['collection_enabled'] = False
            body = (json.dumps(cfg, indent=2) + '\n').encode('utf-8')
            (root / 'config.json').write_bytes(body)
            matches = [match(str(900 + n)) for n in range(10)]
            for row in matches:
                row['unknown_future_field'] = {'keep': ['all fields', None, 123]}
            (root / 'synthetic_http_reply.json').write_text(json.dumps(matches), encoding='utf-8')

            def cli(*args, mocked=False):
                command = [sys.executable, '-c', MOCK_HTTP_CLI] if mocked else [sys.executable, 'scripts/tracker.py', *args]
                result = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.stdout

            output = cli(mocked=True)
            self.assertIn('Viewer contains 10 distinct archived matches.', output)
            self.assertIn('"status": "success"', output)
            folder = root / 'data/fc27/common-gen5/100'
            original = {p.name: p.read_bytes() for p in (folder / 'matches').glob('*.json')}
            self.assertEqual(len(original), 10)
            verify = json.loads(cli('verify'))['archives'][0]
            self.assertEqual(verify['matches'], 10)
            self.assertEqual(verify['revisions'], 10)
            self.assertEqual(verify['snapshots'], 2)
            self.assertEqual(verify['http_responses'], 2)
            payload = json.loads((root / 'site/data/index.json').read_bytes())
            self.assertEqual(len(payload['matches']), 10)
            self.assertEqual(payload['matches'][0]['players'][0]['second_assists'], 2)
            self.assertEqual(payload['matches'][0]['players'][0]['dribbles_completed'], 9)

            backup = json.loads(cli('backup'))
            cli('verify-backup', backup['backup'])
            with zipfile.ZipFile(backup['backup']) as z:
                self.assertEqual(z.read('config.json'), body)
                for name, raw in original.items():
                    self.assertEqual(z.read(f'data/fc27/common-gen5/100/matches/{name}'), raw)
                    self.assertEqual(json.loads(raw)['raw']['unknown_future_field'],
                                     {'keep': ['all fields', None, 123]})
            output = cli(mocked=True)
            self.assertIn('"changed": 0', output)
            self.assertEqual({p.name: p.read_bytes() for p in (folder / 'matches').glob('*.json')}, original)
            cli('verify')
            cli('build')
            self.assertEqual((root / 'config.json').read_bytes(), body)


if __name__ == '__main__':
    unittest.main()
