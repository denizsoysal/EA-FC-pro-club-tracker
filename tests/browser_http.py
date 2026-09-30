#!/usr/bin/env python3
"""Optional real-loopback HTTP/Chromium checks with the SHIPPED CSP intact.

The site is served beneath a repository subpath, the way GitHub Pages hosts a
project site. Uses synthetic data only; no requests to EA or GitHub. Requires
Playwright and Chromium, neither is required by the tracker itself.
"""
import argparse
import copy
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'EA-FC-pro-club-tracker'
sys.path.insert(0, str(Path(__file__).resolve().parent))
import multiclub_fixtures as f  # noqa: E402


class QuietHandler(SimpleHTTPRequestHandler):
    delays = {}  # URL path suffix -> seconds, to hold one club's answer back.

    def log_message(self, *_): pass

    def do_GET(self):
        for suffix, seconds in self.delays.items():
            if self.path.split('?')[0].endswith(suffix):
                time.sleep(seconds)
        try:
            super().do_GET()
        except (ConnectionError, BrokenPipeError):
            pass  # The browser abandoned this request, as the viewer intends.


def publish(site, payload):
    """Write a one-club catalog and dataset exactly where the generated site puts them."""
    club = payload['club']
    folder = site / 'data' / 'clubs' / club['club_id']
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'index.json').write_text(json.dumps(payload), encoding='utf-8')
    (site / 'data' / 'clubs.json').write_text(json.dumps(
        {'schema_version': 1, 'default_club_id': club['club_id'], 'clubs': [club]}), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--screenshots', type=Path)
    args = parser.parse_args()
    if args.screenshots: args.screenshots.mkdir(parents=True, exist_ok=True)
    checks = []
    def check(condition, label):
        if not condition: raise AssertionError(label)
        checks.append(label); print('PASS:', label)
    with tempfile.TemporaryDirectory() as directory:
        served = Path(directory) / 'served'
        site = served / REPOSITORY; shutil.copytree(ROOT / 'site', site, ignore=shutil.ignore_patterns('data'))
        demo = json.loads((site / 'demo.json').read_bytes())
        publish(site, demo)
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(served)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_port}/{REPOSITORY}/'
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, executable_path=os.environ.get('CHROME_BIN') or shutil.which('chromium'), args=['--no-sandbox'])
                errors, console_errors, requests, failed = [], [], [], []

                def open_page(context=None):
                    page = (context or browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)).new_page()
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('console', lambda message: console_errors.append(message.text) if message.type == 'error' else None)
                    page.on('request', lambda request: requests.append(request.url))
                    page.on('requestfailed', lambda request: failed.append(request.url))
                    return page

                page = open_page()
                response = page.goto(base, wait_until='networkidle')
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
                check('Successful dribbles' in page.locator('#player-head').text_content(), 'dribble successes explicitly labelled')
                check('NOT total attempts' in page.locator('button[data-sort="dribbles_completed"]').get_attribute('title'), 'attempt-versus-success distinction in tooltip')
                if args.screenshots: page.screenshot(path=str(args.screenshots / 'dribbling-desktop.png'), full_page=True)
                page.get_by_role('tab', name='Defending', exact=True).click()
                check('Ball recovered (dispossessions)' in page.locator('#player-head').text_content(), 'defensive relabel visible')
                if args.screenshots: page.screenshot(path=str(args.screenshots / 'defending-desktop.png'), full_page=True)
                page.set_viewport_size({'width': 390, 'height': 844})
                page.get_by_role('tab', name='Passing', exact=True).click()
                check(page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'mobile actual HTTP layout stays within viewport')
                if args.screenshots: page.screenshot(path=str(args.screenshots / 'passing-mobile.png'), full_page=True)
                # A plain rebuild payload must not render the removed health copy.
                passive = copy.deepcopy(demo); passive['collection'] = {'status': 'not_requested', 'message': 'Built from the existing archive; no API health claim.'}
                publish(site, passive)
                page.reload(wait_until='networkidle')
                text = page.locator('body').inner_text()
                check(all(term not in text for term in ['not requested', 'Built from the existing archive', 'No API fetch in this build', 'Know what the numbers mean', 'Every match you save', 'Event coverage']), 'removed boilerplate absent from rendered production view')
                page.goto(base + '?demo=1', wait_until='networkidle')
                check(page.locator('#demo-banner').is_visible() and page.locator('#player-body tr').count() == 5, 'synthetic demo loads beneath the repository subpath')
                page.context.close()

                # Three synthetic clubs, produced by the real collector and site builder.
                project = Path(directory) / 'project'
                f.publish(project)
                shutil.rmtree(site / 'data'); shutil.copytree(project / 'site' / 'data', site / 'data')
                requests.clear(); failed.clear()
                page = open_page()
                name = lambda: page.locator('#club-name').inner_text()
                count = lambda: page.locator('#match-count').inner_text()
                def choose(club_id, settle=True):
                    page.select_option('#club-select', club_id)
                    if settle: page.wait_for_function('id => phase !== "loading" && club.club_id === id', arg=club_id)

                response = page.goto(base, wait_until='networkidle')
                check(response.status == 200 and (name(), count()) == ('Alpha United', '3'), 'default club loads beneath the repository subpath')
                wanted = [url for url in requests if 'favicon' not in url]
                check(all(url.startswith(base) for url in wanted) and base + 'data/clubs.json' in wanted, 'every viewer request stays inside the repository subpath')
                check(base + 'data/clubs/100/index.json' in wanted and not any('/clubs/200/' in url or '/clubs/300/' in url for url in wanted), 'only the selected club dataset is requested')
                check(not any('ea.com' in url for url in requests), 'the browser never calls EA')
                choose(f.BETA)
                check((name(), count()) == ('Beta Town', '2') and page.url == base + '?club=200', 'selecting a club loads its data and writes ?club= under the subpath')
                check(base + 'data/clubs/200/index.json' in requests, 'the newly selected dataset comes from its own relative path')
                with page.expect_download() as event: page.locator('#export').click()
                rows = Path(event.value.path()).read_text(encoding='utf-8-sig').splitlines()
                check(event.value.suggested_filename == 'beta-town-200-player-matches.csv' and rows[0].startswith('club_id,club_name,match_id')
                      and all(row.startswith('200,Beta Town,') for row in rows[1:]) and len(rows) == 6, 'all-match CSV download is named for and contains only the selected club')
                with page.expect_download() as event: page.locator('#export-view').click()
                exported = Path(event.value.path()).read_text(encoding='utf-8-sig')
                check(event.value.suggested_filename == 'beta-town-200-overview-totals.csv' and '200,Beta Town,500,Shared Striker,1,totals,3,' in exported, 'view CSV download is named for and labelled with the selected club')
                if args.screenshots: page.screenshot(path=str(args.screenshots / 'clubs-beta-http.png'), full_page=True)
                page.reload(wait_until='networkidle')
                check((name(), count()) == ('Beta Town', '2'), 'the shareable address survives a reload')
                page.goto(base, wait_until='networkidle')
                check((name(), count()) == ('Beta Town', '2'), 'the last selected club is remembered in real localStorage')
                page.goto(base + 'index.html?club=100', wait_until='networkidle')
                check((name(), count()) == ('Alpha United', '3'), 'a ?club= address wins over the remembered club, also via index.html')

                # Hold Beta's answer back: the real fetch is abandoned when another club is chosen.
                QuietHandler.delays = {'/data/clubs/200/index.json': 0.8}
                failed.clear()
                choose(f.BETA, settle=False)
                check(name() == 'Beta Town' and count() == '0' and 'Alpha Anchor' not in page.locator('main').inner_text(), 'a slow club shows a loading state, not the previous club')
                choose(f.GAMMA)
                page.wait_for_timeout(1300)
                check((name(), count()) == ('Gamma Empty', '0') and 'No matches archived yet for Gamma Empty' in page.locator('#player-body').inner_text()
                      and 'Beta Runner' not in page.locator('main').inner_text() and page.evaluate('phase') == 'ready'
                      and not page.locator('#load-error').is_visible(), 'the slow answer never replaces the club selected after it')
                check(any(url.endswith('/data/clubs/200/index.json') for url in failed), 'the abandoned request is cancelled')
                QuietHandler.delays = {}
                page.context.close()

                page = open_page()
                requests.clear()
                page.goto(base + '?club=..%2F200', wait_until='networkidle')
                check((name(), count()) == ('Alpha United', '3') and not any('/clubs/200/' in url for url in requests), 'an unsafe ?club= value is ignored on a first visit')
                page.context.close()

                # Browser may request an optional favicon; ignore that unrelated 404.
                csp = [error for error in console_errors if 'Content Security Policy' in error or 'Refused to' in error]
                check(not errors and not csp, f'no JavaScript/CSP failures: {errors + csp}')
                browser.close()
        finally:
            server.shutdown(); server.server_close()
    print(f'\n{len(checks)} loopback HTTP/browser checks passed. No live EA or GitHub deployment was tested.')

if __name__ == '__main__': main()
