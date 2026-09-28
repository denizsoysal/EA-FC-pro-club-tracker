/* Offline logic tests. Run: node --test tests/test_stats.cjs */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const S = require('../site/assets/stats.js');
const p = (extra = {}) => ({id: '1', name: 'Synthetic', goals: 2, assists: 1, rating: 8,
  events_status: 'parsed:community_mapping', ...extra});
const m = (players, extra = {}) => ({id: '10', timestamp: '2026-09-28T10:00:00Z',
  match_types: ['leagueMatch'], players, ...extra});
const total = (players, opts) => S.playerTotals(players.map(player => m([player])), opts)[0];

test('four views and no crosses or invented key-pass metric', () => {
  assert.deepEqual(Object.keys(S.VIEWS), ['overview', 'passing', 'dribbling', 'defending']);
  assert(!Object.keys(S.METRICS).some(key => /cross|key_pass|beaten_by_pass/.test(key)));
});
test('goals and assists are summed; rating is always averaged', () => {
  const a = total([p(), p({goals: 4, assists: 2, rating: 6})]);
  assert.equal(a.goals, 6); assert.equal(a.assists, 3); assert.equal(a.rating, 7);
});
test('missing metrics stay null, not zero', () => {
  const a = total([p(), p()]);
  assert.equal(a.second_assists, null); assert.equal(a.interceptions, null);
});
test('a genuine zero counts as an available appearance', () => {
  const a = total([p({second_assists: 0}), p({second_assists: null})]);
  assert.equal(a.second_assists, 0); assert.equal(a.counts.second_assists, 1);
});
test('per-match denominator is available appearances, not all appearances', () => {
  const a = total([p({second_assists: 4}), p({second_assists: null}), p({second_assists: 2})], {perMatch: true});
  assert.equal(a.second_assists, 3); assert.equal(a.appearances, 3); assert.equal(a.counts.second_assists, 2);
});
test('ordinary pass percentage is weighted, not a mean of percentages', () => {
  const a = total([p({passes_made: 1, pass_attempts: 1}), p({passes_made: 0, pass_attempts: 9})]);
  assert.equal(a.pass_rate, 10);
});
test('forward percentage is weighted and unchanged by per-match toggle', () => {
  const players = [p({forward_passes_completed: 1, forward_pass_attempts: 1}), p({forward_passes_completed: 0, forward_pass_attempts: 9})];
  assert.equal(total(players).forward_pass_rate, 10);
  assert.equal(total(players, {perMatch: true}).forward_pass_rate, 10);
});
test('long percentage uses only paired available counts', () => {
  const a = total([p({long_passes_completed: 4, long_pass_attempts: 5}), p({long_passes_completed: 20, long_pass_attempts: null})]);
  assert.equal(a.long_pass_rate, 80); assert.equal(a.counts.long_pass_rate, 1);
});
test('zero attempts is undefined rather than 0%', () => {
  const a = total([p({passes_made: 0, pass_attempts: 0})]);
  assert.equal(a.pass_rate, null); assert.equal(a.counts.pass_rate, 1);
  assert.equal(S.ratio(0, 0), null);
});
test('zero completions with positive attempts gives a real 0%', () => {
  assert.equal(total([p({passes_made: 0, pass_attempts: 4})]).pass_rate, 0);
});
test('invalid pass pairs do not inflate the numerator or coverage', () => {
  const a = total([p({passes_made: 20, pass_attempts: 5}), p({passes_made: 3, pass_attempts: 4})]);
  assert.equal(a.pass_rate, 75); assert.equal(a.counts.pass_rate, 1);
});
test('counts do not accidentally include missing values as zero', () => {
  const a = total([p({interceptions: 5}), p({interceptions: null}), p({interceptions: 1})]);
  assert.equal(a.interceptions, 6); assert.equal(a.counts.interceptions, 2);
});
test('invalid numbers are not included', () => {
  for (const invalid of [-1, NaN, Infinity, '7', false]) {
    assert.equal(total([p({goals: invalid})]).goals, null);
  }
});
test('event coverage is not exposed in aggregate rows', () => {
  const a = total([p({dribble_beats: null}), p({events_status: 'unavailable:empty_buckets'})]);
  assert(!('coverage' in a)); assert.equal(a.dribble_beats, null);
});
test('same names are separate players; same ID uses latest name', () => {
  const rows = S.playerTotals([m([p({name: 'New name'}), p({id: '2', name: 'Synthetic'})]), m([p()])]);
  assert.equal(rows.length, 2); assert.equal(rows[0].name, 'New name'); assert.equal(rows[0].appearances, 2);
});
test('nulls sort last in both directions', () => {
  const rows = [{name: 'Missing', goals: null}, {name: 'Low', goals: 1}, {name: 'High', goals: 4}];
  assert.equal(S.sortPlayers(rows, 'goals', 1).at(-1).name, 'Missing');
  assert.equal(S.sortPlayers(rows, 'goals', -1).at(-1).name, 'Missing');
});
test('last 5 is applied after competition filtering and chronological sorting', () => {
  const rows = Array.from({length: 20}, (_, i) => m([], {id: String(i), timestamp: new Date(Date.UTC(2026, 8, i + 1)).toISOString(), match_types: [i % 2 ? 'leagueMatch' : 'friendlyMatch']}));
  const selected = S.filterMatches(rows, {period: 'last5', competition: 'leagueMatch'});
  assert.deepEqual(selected.map(row => row.id), ['19', '17', '15', '13', '11']);
});
test('date filter excludes absent timestamps; all-history includes them', () => {
  const rows = [m([], {timestamp: null}), m([])];
  assert.equal(S.filterMatches(rows, {period: '7', now: Date.parse('2026-09-29T00:00:00Z')}).length, 1);
  assert.equal(S.filterMatches(rows).length, 2);
});
test('single-match ratio computed from counts without aggregate toggle', () => {
  assert.equal(S.singleValue(p({forward_passes_completed: 3, forward_pass_attempts: 4}), 'forward_pass_rate'), 75);
  assert.equal(S.singleValue(p(), 'dribble_beats'), null);
});
test('CSV escapes formula prefixes, quotes, commas and line breaks', () => {
  assert.equal(S.csvCell(' =SUM(A1)'), "' =SUM(A1)");
  assert.equal(S.csvCell('@test'), "'@test");
  assert.equal(S.csvCell('a,"b"\nc'), '"a,""b""\nc"');
  assert.equal(S.csvCell(null), '');
});
test('view export respects selected keys, missing values and available-match counts', () => {
  const rows = S.playerTotals([m([p({second_assists: 0, name: '=unsafe'})])]);
  const csv = S.tableCsv(rows, 'overview');
  assert(csv.startsWith('\ufeff')); assert(csv.includes("'=unsafe"));
  assert(csv.includes('second_assists_available_matches'));
  assert(!csv.includes('interceptions')); assert(!csv.includes('cross'));
  assert(csv.includes(',0,1,'));
});
test('demo has every displayed normalized key and labels itself synthetic', () => {
  const data = JSON.parse(fs.readFileSync(path.join(__dirname, '../site/demo.json'), 'utf8'));
  assert.equal(data.schema_version, 2); assert.equal(data.collection.status, 'demo');
  for (const match of data.matches) for (const player of match.players) {
    for (const key of Object.keys(S.METRICS)) assert(key in player, `Missing normalized key: ${key}`);
  }
});


test('passing view contains only completed passes, pass percentage and through balls', () => {
  assert.deepEqual(S.VIEWS.passing.keys, ['passes_made', 'pass_rate', 'through_balls_completed']);
});
test('view exports have no event coverage, forward pass or long pass column', () => {
  for (const view of Object.keys(S.VIEWS)) {
    const csv = S.tableCsv(S.playerTotals([m([p()])]), view);
    assert(!/advanced_records|event_coverage|forward_pass|long_pass/.test(csv));
  }
});
test('dribble label distinguishes reported successes from attempts', () => {
  assert.equal(S.METRICS.dribbles_completed.label, 'Successful dribbles');
  assert(S.METRICS.dribbles_completed.description.includes('NOT total attempts'));
});
test('ball recovered is explicitly restricted to dispossessions', () => {
  assert.equal(S.METRICS.opponents_dispossessed.label, 'Ball recovered (dispossessions)');
  assert(S.METRICS.opponents_dispossessed.description.includes('not a verified total'));
});
