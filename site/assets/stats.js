/* Pure statistics logic shared by the viewer and Node's built-in test runner.
 * No dependencies. Counts never infer time played or turn missing values into 0.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ClubStats = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const finite = n => typeof n === "number" && Number.isFinite(n) && n >= 0;
  const metric = (label, description, advanced = false, kind = "count") => ({label, description, advanced, kind});
  const METRICS = {
    goals: metric("Goals", "Goals from the ordinary player-match API field."),
    assists: metric("Assists", "Assists from the ordinary player-match API field."),
    second_assists: metric("2nd assists", "Community event 115: the pass before the assist. Not a key-pass counter.", true),
    shots: metric("Shots", "The ordinary API shots field. Not reconstructed by adding on-target and off-target events."),
    shots_on_target: metric("On target", "Community event 217: shots on target.", true),
    rating: metric("Avg. rating", "Mean of available match ratings. Never summed; missing ratings are excluded.", false, "average"),
    passes_made: metric("Passes made", "Completed passes from the ordinary API field."),
    pass_rate: metric("Pass %", "100 × completed / attempted passes, summed over records with both valid counts. Not an average of match percentages.", false, "rate"),
    forward_pass_rate: metric("Forward %", "100 × event 30 / (event 30 + event 31), using totals over valid records.", true, "rate"),
    long_pass_rate: metric("Long %", "100 × event 28 / (event 28 + event 29), using totals over valid records.", true, "rate"),
    through_balls_completed: metric("Through balls", "Community event 152: successful through balls only, not all attempts.", true),
    dribbles_completed: metric("Successful dribbles", "Community event 174: reported completed/successful dribbles, NOT total attempts. EA’s exact definition is not documented here; this is not a count of successful one-on-one take-ons. See the separate opponents-beaten columns.", true),
    dribble_beats: metric("Opponents beaten", "Community event 112 − event 38: non-skill dribble beats. Counts actions, not distinct defenders. Skill-move beats are separate; negative differences are unavailable.", true),
    skill_move_beats: metric("Skill-move beats", "Community event 38: opponents beaten using skill moves. Kept separate from non-skill dribble beats; not a count of unique players.", true),
    interceptions: metric("Interceptions", "Community event 6: interceptions.", true),
    tackles_won: metric("Tackles won", "Community events 229 + 230: standing plus sliding tackles won. Not inferred from the ordinary tackles-made field.", true),
    standing_tackles_won: metric("Standing won", "Community event 229: standing tackles won. A component of tackles won, not an additional total.", true),
    sliding_tackles_won: metric("Sliding won", "Community event 230: sliding tackles won. A component of tackles won, not an additional total.", true),
    opponents_dispossessed: metric("Ball recovered (dispossessions)", "Community event 158: dispossessing an opponent, not losing the ball yourself. This label refers ONLY to dispossessions; it is not a verified total of all loose-ball recoveries. May overlap tackles and interceptions; do not add them together.", true),
  };
  const VIEWS = {
    overview: {
      label: "Overview", defaultSort: "goals",
      keys: ["goals", "assists", "second_assists", "shots", "shots_on_target", "rating"],
      note: "Goals, creation and shooting. Second assists are the pass before the assist—not key passes.",
    },
    passing: {
      label: "Passing", defaultSort: "through_balls_completed",
      keys: ["passes_made", "pass_rate", "through_balls_completed"],
      note: "Completion percentages are weighted by attempts. Through balls count successful passes only.",
    },
    dribbling: {
      label: "Dribbling", defaultSort: "dribble_beats",
      keys: ["dribbles_completed", "dribble_beats", "skill_move_beats"],
      note: "Successful dribbles = reported completions, not attempts. Beating an opponent is counted separately; skill-move beats are separate from non-skill beats.",
    },
    defending: {
      label: "Defending", defaultSort: "tackles_won",
      keys: ["interceptions", "tackles_won", "standing_tackles_won", "sliding_tackles_won", "opponents_dispossessed"],
      note: "Tackles won = standing + sliding. Ball recovered counts dispossessions only, not all recoveries; defensive counts may overlap.",
    },
  };
  const RATES = {
    pass_rate: ["passes_made", "pass_attempts"],
    forward_pass_rate: ["forward_passes_completed", "forward_pass_attempts"],
    long_pass_rate: ["long_passes_completed", "long_pass_attempts"],
  };
  const countKeys = Object.keys(METRICS).filter(key => METRICS[key].kind !== "rate");
  function ratio(completed, attempted) {
    return finite(completed) && finite(attempted) && attempted > 0 && completed <= attempted
      ? 100 * completed / attempted : null;
  }
  function filterMatches(matches, {period = "all", competition = "all", now = Date.now()} = {}) {
    let result = matches.filter(m => competition === "all" || (m.match_types || []).includes(competition));
    result = [...result].sort((a, b) => (Date.parse(b.timestamp) || 0) - (Date.parse(a.timestamp) || 0));
    if (period === "last5" || period === "last10") return result.slice(0, period === "last5" ? 5 : 10);
    if (period !== "all" && Number(period) > 0) {
      const cutoff = now - Number(period) * 86400000;
      result = result.filter(m => Date.parse(m.timestamp) >= cutoff);
    }
    return result;
  }
  function playerTotals(matches, {perMatch = false} = {}) {
    const people = new Map();
    // Call with newest-first records, as filterMatches does, to keep latest names.
    for (const match of matches) for (const p of match.players || []) {
      const id = String(p.id);
      if (!people.has(id)) people.set(id, {
        id, name: String(p.name || id), appearances: 0, sums: {}, counts: {}, pairs: {},
      });
      const a = people.get(id);
      a.appearances++;
      for (const key of countKeys) if (finite(p[key])) {
        a.sums[key] = (a.sums[key] || 0) + p[key];
        a.counts[key] = (a.counts[key] || 0) + 1;
      }
      for (const [key, [made, attempted]] of Object.entries(RATES)) {
        if (finite(p[made]) && finite(p[attempted]) && p[made] <= p[attempted]) {
          const pair = a.pairs[key] || (a.pairs[key] = {made: 0, attempted: 0});
          pair.made += p[made]; pair.attempted += p[attempted];
          a.counts[key] = (a.counts[key] || 0) + 1;
        }
      }
    }
    return [...people.values()].map(a => {
      for (const key of countKeys) {
        a[key] = a.counts[key] ? a.sums[key] / ((perMatch || key === "rating") ? a.counts[key] : 1) : null;
      }
      for (const key of Object.keys(RATES)) {
        const pair = a.pairs[key]; a[key] = pair ? ratio(pair.made, pair.attempted) : null;
      }
      return a;
    });
  }
  function singleValue(p, key) {
    if (RATES[key]) return ratio(p[RATES[key][0]], p[RATES[key][1]]);
    return finite(p[key]) ? p[key] : null;
  }
  function sortPlayers(players, key, direction = -1) {
    return [...players].sort((a, b) => {
      if (key === "name") return direction * a.name.localeCompare(b.name);
      if (!finite(a[key]) && !finite(b[key])) return a.name.localeCompare(b.name);
      if (!finite(a[key])) return 1;
      if (!finite(b[key])) return -1;
      return direction * (a[key] - b[key]) || a.name.localeCompare(b.name);
    });
  }
  function csvCell(value) {
    let text = value === null || value === undefined ? "" : String(value);
    if (/^\s*[=+@-]/.test(text)) text = "'" + text;
    return /[",\r\n]/.test(text) ? '"' + text.replaceAll('"', '""') + '"' : text;
  }
  function tableCsv(players, view, {perMatch = false} = {}) {
    const keys = VIEWS[view].keys;
    const header = ["player_id", "player_name", "appearances", "mode"];
    for (const key of keys) header.push(key, key + "_available_matches");
    const rows = [header];
    for (const p of players) {
      const row = [p.id, p.name, p.appearances, perMatch ? "per_available_match" : "totals"];
      for (const key of keys) row.push(p[key], p.counts[key] || 0);
      rows.push(row);
    }
    return "\ufeff" + rows.map(row => row.map(csvCell).join(",")).join("\r\n") + "\r\n";
  }
  return {finite, METRICS, VIEWS, RATES, ratio, filterMatches, playerTotals, singleValue, sortPlayers, csvCell, tableCsv};
});
