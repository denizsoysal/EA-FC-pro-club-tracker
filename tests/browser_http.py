#!/usr/bin/env python3
"""Optional real-loopback HTTP/Chromium checks with the SHIPPED CSP intact.

Uses synthetic data only; no requests to EA or GitHub. Requires Playwright and
Chromium, neither is required by the tracker itself.
"""
import argparse
import copy
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]

class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_): pass


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--screenshots', type=Path)
    args = parser.parse_args()
    if args.screenshots: args.screenshots.mkdir(parents=True, exist_ok=True)
    checks = []
    def check(condition, label):
        if not condition: raise AssertionError(label)
        checks.append(label); print('PASS:', label)
    with tempfile.TemporaryDirectory() as directory:
        site = Path(directory) / 'site'; shutil.copytree(ROOT / 'site', site)
        demo = json.loads((site / 'demo.json').read_bytes())
        (site / 'data').mkdir(exist_ok=True)
        (site / 'data/index.json').write_text(json.dumps(demo), encoding='utf-8')
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(site)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, executable_path=os.environ.get('CHROME_BIN') or shutil.which('chromium'), args=['--no-sandbox'])
                page = browser.new_page(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
                errors, console_errors = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('console', lambda message: console_errors.append(message.text) if message.type == 'error' else None)
                response = page.goto(f'http://127.0.0.1:{server.server_port}/', wait_until='networkidle')
                check(response.status == 200, 'real HTTP document request succeeds')
                check(page.locator('meta[http-equiv="Content-Security-Policy"]').count() == 1, 'shipped CSP remains in place')
                check(page.locator('#player-body tr').count() == 5, 'scripts and JSON load through the real HTTP/CSP path')
                check(page.title() == 'DubsFC Tracker', 'new title loads without a build tool')
                page.get_by_role('tab', name='Passing', exact=True).click()
                head = page.locator('#player-head').inner_text()
                check('Forward' not in head and 'Long' not in head and 'Event coverage' not in head, 'passing table omits removed columns')
                with page.expect_download() as event: page.locator('#export-view').click()
                exported = Path(event.value.path()).read_text(encoding='utf-8-sig')
                check('through_balls_completed' in exported and 'forward_pass' not in exported and 'advanced_records' not in exported, 'actual CSV download has requested columns')
                if args.screenshots: page.screenshot(path=str(args.screenshots / 'passing-desktop.png'), full_page=True)
                page.get_by_role('tab', name='Dribbling', exact=True).click()
                check('Successful dribbles' in page.locator('#player-head').inner_text(), 'dribble successes explicitly labelled')
                check('NOT total attempts' in page.locator('button[data-sort="dribbles_completed"]').get_attribute('title'), 'attempt-versus-success distinction in tooltip')
                if args.screenshots: page.screenshot(path=str(args.screenshots / 'dribbling-desktop.png'), full_page=True)
                page.get_by_role('tab', name='Defending', exact=True).click()
                check('Ball recovered (dispossessions)' in page.locator('#player-head').inner_text(), 'defensive relabel visible')
                if args.screenshots: page.screenshot(path=str(args.screenshots / 'defending-desktop.png'), full_page=True)
                page.set_viewport_size({'width': 390, 'height': 844})
                page.get_by_role('tab', name='Passing', exact=True).click()
                check(page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'mobile actual HTTP layout stays within viewport')
                if args.screenshots: page.screenshot(path=str(args.screenshots / 'passing-mobile.png'), full_page=True)
                # A plain rebuild payload must not render the removed health copy.
                passive = copy.deepcopy(demo); passive['collection'] = {'status': 'not_requested', 'message': 'Built from the existing archive; no API health claim.'}
                (site / 'data/index.json').write_text(json.dumps(passive), encoding='utf-8')
                page.reload(wait_until='networkidle')
                text = page.locator('body').inner_text()
                check(all(term not in text for term in ['not requested', 'Built from the existing archive', 'No API fetch in this build', 'Know what the numbers mean', 'Every match you save', 'Event coverage']), 'removed boilerplate absent from rendered production view')
                # Browser may request an optional favicon; ignore that unrelated 404.
                csp = [error for error in console_errors if 'Content Security Policy' in error or 'Refused to' in error]
                check(not errors and not csp, f'no JavaScript/CSP failures: {errors + csp}')
                browser.close()
        finally:
            server.shutdown(); server.server_close()
    print(f'\n{len(checks)} loopback HTTP/browser checks passed. No live EA or GitHub deployment was tested.')

if __name__ == '__main__': main()
