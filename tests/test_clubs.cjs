/* Offline club-selection tests. Run: node --test tests/test_clubs.cjs */
const test = require('node:test');
const assert = require('node:assert/strict');
const C = require('../site/assets/clubs.js');
const S = require('../site/assets/stats.js');

const club = (club_id, club_name) => ({club_id, club_name, platform: 'common-gen5', edition: 'fc27'});
const catalog = C.parseCatalog({schema_version: 1, default_club_id: '100',
  clubs: [club('100', 'Alpha United'), club('200', 'Beta Town')]});
const UNSAFE = ['../200', '200/../100', '200/index', '/etc/passwd', 'https://example.invalid/x', '%2e%2e', '200 ',
  ' 200', '2e2', '-200', '0x64', '', '999', 'null', '__proto__', 'constructor'];

test('catalog keeps configured clubs in order with their default', () => {
  assert.deepEqual(catalog.clubs.map(entry => entry.club_id), ['100', '200']);
  assert.equal(catalog.default_club_id, '100');
  assert.equal(C.parseCatalog({default_club_id: '200', clubs: catalog.clubs}).default_club_id, '200');
});
test('a default that is not configured falls back to the first club', () => {
  assert.equal(C.parseCatalog({default_club_id: '999', clubs: catalog.clubs}).default_club_id, '100');
  assert.equal(C.parseCatalog({clubs: catalog.clubs}).default_club_id, '100');
  assert.equal(C.parseCatalog({clubs: []}).default_club_id, '');
});
test('malformed catalogs are rejected instead of guessed at', () => {
  for (const bad of [null, [], {}, {clubs: 'x'}, {clubs: [null]}, {clubs: [{club_id: 100}]}, {clubs: [club('../200', 'x')]},
    {clubs: [club('100', 'a'), club('100', 'b')]}, {clubs: [{club_name: 'no id'}]}]) {
    assert.throws(() => C.parseCatalog(bad), /Unexpected club catalog format/);
  }
});
test('a missing club name still yields a label that identifies the club', () => {
  assert.equal(C.parseCatalog({clubs: [{club_id: '300'}]}).clubs[0].club_name, 'Club 300');
});
test('first visit opens the default club', () => {
  assert.equal(C.resolveClub(catalog).club_id, '100');
  assert.equal(C.resolveClub(catalog, {requested: null, remembered: null}).club_id, '100');
});
test('a valid address-bar club wins over the remembered club', () => {
  assert.equal(C.resolveClub(catalog, {requested: '200', remembered: '100'}).club_id, '200');
  assert.equal(C.resolveClub(catalog, {requested: '100', remembered: '200'}).club_id, '100');
});
test('the remembered club is used when the address names none or an invalid one', () => {
  assert.equal(C.resolveClub(catalog, {remembered: '200'}).club_id, '200');
  assert.equal(C.resolveClub(catalog, {requested: '999', remembered: '200'}).club_id, '200');
  assert.equal(C.resolveClub(catalog, {requested: '../200', remembered: '200'}).club_id, '200');
});
test('invalid address and invalid remembered values fall back to the default', () => {
  for (const value of UNSAFE) {
    assert.equal(C.resolveClub(catalog, {requested: value, remembered: value}).club_id, '100', value);
    assert.equal(C.findClub(catalog, value), null, value);
  }
  for (const value of [200, null, undefined, ['200'], {club_id: '200'}, true]) assert.equal(C.findClub(catalog, value), null);
});
test('an empty catalog resolves to no club', () => {
  assert.equal(C.resolveClub(C.parseCatalog({clubs: []}), {requested: '100', remembered: '100'}), null);
});
test('data paths exist only for configured clubs and stay relative', () => {
  assert.equal(C.dataPath(catalog, '200'), 'data/clubs/200/index.json');
  assert.equal(C.csvPath(catalog, '200'), 'data/clubs/200/player_matches_200.csv');
  for (const path of [C.dataPath(catalog, '100'), C.csvPath(catalog, '100')]) {
    assert(!path.startsWith('/') && !path.includes('..') && !path.includes('//'), path);
  }
  for (const value of UNSAFE) {
    assert.throws(() => C.dataPath(catalog, value), /not in the published catalog/, value);
    assert.throws(() => C.csvPath(catalog, value), /not in the published catalog/, value);
  }
});
test('relative data paths resolve beneath a GitHub Pages repository subpath', () => {
  for (const page of ['https://user.github.io/EA-FC-pro-club-tracker/', 'https://user.github.io/EA-FC-pro-club-tracker/?club=200',
    'https://user.github.io/EA-FC-pro-club-tracker/index.html#players']) {
    assert.equal(new URL(C.dataPath(catalog, '200'), page).href, 'https://user.github.io/EA-FC-pro-club-tracker/data/clubs/200/index.json');
    assert.equal(new URL('data/clubs.json', page).pathname, '/EA-FC-pro-club-tracker/data/clubs.json');
  }
});
test('export file names identify the club by name and ID', () => {
  assert.equal(C.fileSlug(club('696276', 'Dubs VzeDoux')), 'dubs-vzedoux-696276');
  assert.equal(C.fileSlug(club('976704', 'FEN V12')), 'fen-v12-976704');
  assert.equal(C.fileSlug(club('300', '../../etc/passwd')), 'etc-passwd-300');
  assert.equal(C.fileSlug(club('300', '⚽⚽')), 'club-300');
  assert.equal(C.fileSlug(club('300', 'Águilas "FC"')), 'aguilas-fc-300');
});
test('view CSV names the club in every row when one is given', () => {
  const row = {id: '500', name: 'Shared Striker', goals: 3, assists: 0, rating: 7.5};
  const players = S.playerTotals([{id: '1', timestamp: null, match_types: ['leagueMatch'], players: [row]}]);
  const lines = S.tableCsv(players, 'shooting', {club: club('200', '=Beta, "Town"')}).replace('﻿', '').trim().split('\r\n');
  assert.equal(lines[0], 'club_id,club_name,player_id,player_name,appearances,mode,goals,goals_available_matches,shots,shots_available_matches,shots_on_target,shots_on_target_available_matches');
  assert(lines[1].startsWith('200,"\'=Beta, ""Town""",500,Shared Striker,1,totals,3,1,'));
  assert(!S.tableCsv(players, 'shooting').includes('club_id'));
});
test('the same player ID in two club datasets is totalled per dataset, never merged', () => {
  const appearance = goals => ({id: '1', timestamp: null, match_types: ['leagueMatch'], players: [{id: '500', name: 'Shared Striker', goals}]});
  const alpha = S.playerTotals([appearance(1), appearance(0), appearance(0)]);
  const beta = S.playerTotals([appearance(3)]);
  assert.deepEqual([alpha[0].goals, alpha[0].appearances], [1, 3]);
  assert.deepEqual([beta[0].goals, beta[0].appearances], [3, 1]);
});
