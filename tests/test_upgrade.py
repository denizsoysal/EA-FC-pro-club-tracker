"""Exercise the actual installed CLI on a synthetic, pre-upgrade 10-match archive."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from test_tracker import t, config, match

ROOT = Path(__file__).resolve().parents[1]


class UpgradeTests(unittest.TestCase):
    def test_existing_config_and_ten_old_match_files_survive_backup_verify_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / 'scripts', root / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copytree(ROOT / 'site', root / 'site', ignore=shutil.ignore_patterns('data'))
            cfg = config(); cfg['collection_enabled'] = False; cfg['custom_preference'] = 'KEEP EXACTLY'
            original_config = (json.dumps(cfg, indent=4) + '\n').encode('utf-8')
            (root / 'config.json').write_bytes(original_config)
            folder = t.archive_dir(root, cfg) / 'matches'; folder.mkdir(parents=True)
            original = {}
            for i in range(10):
                mid = str(900 + i); raw = match(mid); raw['keep_future_field'] = {'unmapped': 123, 'value': None}
                record = dict(schema_version=1, edition=cfg['edition'], platform=cfg['platform'],
                              club_id=cfg['club_id'], match_id=mid, match_types=['leagueMatch'],
                              first_archived_at='2026-09-28T13:35:55+00:00',
                              last_changed_at='2026-09-28T13:35:55+00:00', raw=raw)
                body = (json.dumps(record, indent=4) + '\n').encode('utf-8')
                path = folder / (mid + '.json'); path.write_bytes(body); original[path] = body
            outputs = {}
            for command in ['backup', 'verify', 'build']:
                result = subprocess.run([sys.executable, 'scripts/tracker.py', command], cwd=root, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                outputs[command] = result.stdout
            self.assertEqual((root / 'config.json').read_bytes(), original_config)
            self.assertEqual({p: p.read_bytes() for p in folder.glob('*.json')}, original)
            self.assertEqual(json.loads(outputs['verify'])['archives'][0]['matches'], 10)
            self.assertEqual(len(json.loads((root / 'site/data/index.json').read_bytes())['matches']), 10)
            backup = json.loads(outputs['backup'])['backup']
            with zipfile.ZipFile(backup) as z:
                for path, body in original.items(): self.assertEqual(z.read(path.relative_to(root).as_posix()), body)
                self.assertEqual(z.read('config.json'), original_config)
            first = next(iter(original)); first.unlink()
            verify = subprocess.run([sys.executable, 'scripts/tracker.py', 'verify'], cwd=root, text=True, capture_output=True)
            self.assertEqual(verify.returncode, 1)
            self.assertIn('Missing archived', verify.stderr)


if __name__ == '__main__': unittest.main()
