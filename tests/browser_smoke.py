#!/usr/bin/env python3
"""Optional OFFLINE Chromium DOM checks, not an HTTP/CSP/deployment test.

Requires Playwright + Chromium, not needed by the collector or viewer.
  python -m pip install playwright
  python -m playwright install chromium
  python tests/browser_smoke.py

CHROME_BIN can select an installed Chromium executable. The harness loads the
shipped HTML/CSS/JS into an empty document and mocks fetch. It removes the CSP
only in this test document to permit injecting scripts; shipped files retain CSP.
Browser navigation is intentionally not used. Screenshots are optional:
  python tests/browser_smoke.py --screenshots /tmp/club-preview
"""
from pathlib import Path
import argparse
import copy
import json
import os
import re
import shutil
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'site'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--screenshots', type=Path)
    args = parser.parse_args()
    demo = json.loads((SITE / 'demo.json').read_text())
    # Never depend on or modify a user's real generated archive for the empty test.
    empty = copy.deepcopy(demo)
    empty.update(matches=[], collection={'status': 'not_requested', 'message': 'Synthetic empty fixture.'})
    empty['config'].update(club_id='', club_name='Empty test club')
    html = (SITE / 'index.html').read_text()
    html = re.sub(r'<meta http-equiv="Content-Security-Policy"[^>]*>', '', html)
    html = re.sub(r'<script[^>]*src="[^"]+"[^>]*></script>', '', html)
    html = re.sub(r'<link rel="stylesheet"[^>]*>', '', html)
    checks = []

    def check(condition, label):
        if not condition:
            raise AssertionError(label)
        checks.append(label)
        print('PASS:', label)

    with sync_playwright() as pw:
        executable = os.environ.get('CHROME_BIN') or shutil.which('chromium')
        browser = pw.chromium.launch(headless=True, executable_path=executable,
                                     args=['--no-sandbox'])
        errors = []

        def load(payload, width=1440):
            page = browser.new_page(viewport={'width': width, 'height': 1080},
                                    device_scale_factor=1)
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.set_content(html)
            page.add_style_tag(content=(SITE / 'assets/style.css').read_text())
            page.evaluate('(payload) => {window.fetch=async()=>({ok:true,status:200,json:async()=>payload});}', payload)
            page.add_script_tag(content=(SITE / 'assets/stats.js').read_text())
            page.add_script_tag(content=(SITE / 'assets/app.js').read_text())
            page.wait_for_selector('#player-body tr')
            return page

        page = load(demo)
        check(page.locator('#player-body tr').count() == 5, 'demo renders 5 synthetic players')
        check(page.locator('#demo-banner').is_visible(), 'synthetic payload cannot look like a live archive')
        check(page.get_by_role('tab').count() == 5, 'exactly five metric tabs')
        check(page.title() == 'DubsFC Tracker', 'page title uses requested brand')
        check('DubsFC Tracker' in page.locator('.brand').inner_text(), 'wordmark updated')
        check('Event coverage' not in page.locator('body').inner_text(), 'no event coverage in viewer')
        check('Know what the numbers mean' not in page.locator('body').inner_text(), 'explanatory block removed')
        check('Every match you save' not in page.locator('body').inner_text(), 'hero slogan removed')
        overview_head = page.locator('#player-head').text_content()
        check('Successful dribbles' in overview_head and 'Tackles won' in overview_head, 'overview includes successful dribbles and tackles won')
        check('Shots' not in overview_head and 'On target' not in overview_head, 'overview omits shooting columns')

        if args.screenshots:
            args.screenshots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(args.screenshots / 'overview-desktop.png'), full_page=True)

        for view, expected in [('Shooting', 'On target'), ('Passing', 'Through balls'), ('Dribbling', 'Skill-move beats'), ('Defending', 'Ball recovered (dispossessions)')]:
            tab = page.get_by_role('tab', name=view, exact=True); tab.click()
            check(tab.get_attribute('aria-selected') == 'true' and expected in page.locator('#player-head').text_content(), f'{view} tab changes columns and active state')
            if args.screenshots:
                page.screenshot(path=str(args.screenshots / (view.lower() + '-desktop.png')), full_page=True)
                page.locator('#players').screenshot(path=str(args.screenshots / (view.lower() + '-table.png')))
        check(not re.search(r'cross', page.locator('#player-head').text_content(), re.I), 'no crossing column')
        page.get_by_role('tab', name='Passing', exact=True).click()
        before = page.locator('[data-stat="pass_rate"]').all_inner_texts()
        page.locator('#per-match').check()
        check(page.locator('[data-stat="pass_rate"]').all_inner_texts() == before, 'per-match toggle does not change percentages')
        check('Forward %' not in page.locator('#player-head').inner_text() and 'Long %' not in page.locator('#player-head').inner_text(), 'unrequested pass columns removed')
        page.locator('#player-search').fill('creator')
        check(page.locator('#player-body tr').count() == 1 and 'Creator 10' in page.locator('#player-body').inner_text(), 'player search filters visible rows')
        # Mock only the DOM download click, capture the actual Blob generated.
        page.evaluate('''() => {
          window.__csv = null;
          URL.createObjectURL = blob => {blob.text().then(text => window.__csv = text); return 'blob:offline-test';};
          URL.revokeObjectURL = () => {};
          HTMLAnchorElement.prototype.click = function() {};
        }''')
        page.locator('#export-view').click()
        page.wait_for_function('typeof window.__csv === "string"')
        exported = page.evaluate('window.__csv')
        check('Creator 10' in exported and 'Runner 7' not in exported and 'through_balls_completed' in exported, 'view CSV respects player search and selected category')
        page.locator('#player-search').fill('')
        page.locator('#per-match').uncheck()
        page.locator('#competition').select_option('friendlyMatch')
        page.get_by_role('tab', name='Dribbling', exact=True).click()
        check(page.locator('#match-count').inner_text() == '2', 'competition filter selects demo friendlies')
        check(all(value == '—' for value in page.locator('[data-stat="dribble_beats"]').all_inner_texts()), 'missing friendly event data stays unavailable')
        page.locator('#competition').select_option('all')
        page.locator('#period').select_option('last5')
        check(page.locator('#match-count').inner_text() == '5', 'last-5 archive filter works')
        page.locator('#period').select_option('all')
        page.locator('.match-item > summary').first.click()
        page.wait_for_selector('.match-group')
        check(page.locator('.match-item').first.locator('.match-group').count() == 5, 'expanded match provides all five statistic groups')
        page.locator('.match-item').first.locator('[data-group="passing"] > summary').click()
        check('Forward %' not in page.locator('.match-item').first.locator('[data-group="passing"]').text_content(), 'match detail omits forward and long columns')
        page.get_by_role('tab', name='Overview', exact=True).focus()
        page.get_by_role('tab', name='Overview', exact=True).click()
        page.keyboard.press('ArrowRight')
        check(page.get_by_role('tab', name='Shooting', exact=True).get_attribute('aria-selected') == 'true', 'keyboard arrow navigation selects next tab')

        # Collapse detail and reset viewport for visual inspection.
        page.locator('.match-item > summary').first.click()
        for width in (390, 320):
            page.set_viewport_size({'width': width, 'height': 844})
            check(page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'{width}px mobile has no page-level horizontal overflow')
        page.set_viewport_size({'width': 390, 'height': 844})
        page.get_by_role('tab', name='Dribbling', exact=True).click()
        if args.screenshots:
            page.screenshot(path=str(args.screenshots / 'dribbling-mobile.png'), full_page=True)
        page.close()

        page = load(empty)
        check('No matches archived' in page.locator('#player-body').inner_text(), 'real empty archive is not replaced by demo data')
        check(not page.locator('#demo-banner').is_visible(), 'empty production dataset is not labelled synthetic')
        check(not page.locator('#collector-health').is_visible(), 'no build-only health messages shown')
        page.close()

        error_fixture = copy.deepcopy(empty)
        error_fixture['collection'] = {'status': 'error', 'needs_attention': True, 'message': 'Simulated API failure'}
        page = load(error_fixture)
        check(page.locator('#collector-health').is_visible() and 'Simulated API failure' in page.locator('#health-text').inner_text(), 'real failures remain visible')
        page.close()

        unsafe = copy.deepcopy(demo)
        unsafe['matches'][0]['players'][0]['name'] = '<img src=x onerror="window.pwned=true">'
        page = load(unsafe)
        check(page.locator('#player-body img').count() == 0 and '<img' in page.locator('#player-body').inner_text(), 'untrusted player names render as text, not HTML')
        page.close()
        legacy = copy.deepcopy(demo); legacy['schema_version'] = 1
        for match in legacy['matches']:
            for player in match['players']:
                for key in ['forward_passes_completed', 'forward_pass_attempts', 'dribble_beats', 'interceptions']:
                    player.pop(key, None)
        page = load(legacy)
        page.get_by_role('tab', name='Dribbling', exact=True).click()
        check(all(value == '—' for value in page.locator('[data-stat="dribble_beats"]').all_inner_texts()), 'old viewer payloads tolerate missing newly added fields')
        check(page.locator('#mapping-note').count() == 0, 'community-mapping banner is removed, including for legacy schema payloads')
        page.close()
        check(not errors, 'no JavaScript page errors: ' + repr(errors))
        browser.close()
    print(f'\n{len(checks)} offline DOM checks passed. HTTP, CSP and live EA access are NOT tested.')


if __name__ == '__main__':
    main()
