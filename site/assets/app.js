"use strict";
const $ = id => document.getElementById(id);
const S = window.ClubStats, C = window.ClubCatalog;
const format = (n, digits = 0) => S.finite(n) ? n.toLocaleString(undefined, {maximumFractionDigits: digits, minimumFractionDigits: digits}) : "—";
const labels = {leagueMatch: "League", playoffMatch: "Playoffs", friendlyMatch: "Friendly"};
const STORAGE_KEY = "dubsfc-tracker.club", NO_DATA = {matches: []};
// `archive` only ever holds the selected club's own dataset. It is NO_DATA while
// that dataset loads or after it fails, never another club's numbers.
let catalog = {default_club_id: "", clubs: []}, club = null, archive = NO_DATA, phase = "loading";
let demo = false, loadRun = 0, loadAbort = null;
let activeView = "overview", sortKey = "goals", sortDirection = -1, matchLimit = 20;
function node(tag, text, className) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (className) e.className = className;
  return e;
}
function dateText(value) {
  if (!value) return "Date unavailable";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Date unavailable" : date.toLocaleString(undefined, {month:"short", day:"numeric", year:"numeric", hour:"2-digit", minute:"2-digit"});
}
function filteredMatches() {
  return S.filterMatches(archive.matches, {period: $("period").value, competition: $("competition").value});
}
function emptyText() {
  if (archive.matches.length) return "No archived matches match this filter.";
  if (phase === "setup") return "No club configured yet. Set your club ID in config.json, then run the collector.";
  if (phase === "loading") return club ? `Loading ${club.club_name}…` : "Loading…";
  if (phase === "failed") return club ? `Statistics for ${club.club_name} could not be loaded.` : "Statistics could not be loaded.";
  return `No matches archived yet for ${club.club_name}. They appear here after the collector's next successful run.`;
}
function visiblePlayers(matches) {
  const query = $("player-search").value.trim().toLocaleLowerCase();
  return S.sortPlayers(S.playerTotals(matches, {perMatch: $("per-match").checked})
    .filter(p => p.name.toLocaleLowerCase().includes(query)), sortKey, sortDirection);
}
function metricText(value, key, perMatch = false) {
  const def = S.METRICS[key];
  const digits = def.kind === "rate" ? 1 : def.kind === "average" ? 2 : perMatch ? 2 : 0;
  return format(value, digits) + (def.kind === "rate" && S.finite(value) ? "%" : "");
}
function renderHeader() {
  const row = $("player-head"); row.replaceChildren();
  const headers = [["name", "Player"], ["appearances", "MP"], ...S.VIEWS[activeView].keys.map(key => [key, S.METRICS[key].label])];
  for (const [key, label] of headers) {
    const def = S.METRICS[key], th = node("th");
    th.scope = "col";
    th.setAttribute("aria-sort", key === sortKey ? (sortDirection === 1 ? "ascending" : "descending") : "none");
    const button = node("button", label); button.type = "button"; button.dataset.sort = key;
    if (def) button.title = def.description;
    if (key === sortKey) button.append(node("span", sortDirection === 1 ? " ↑" : " ↓", "sort-arrow"));
    th.append(button); row.append(th);
  }
}
function renderPlayers(matches) {
  renderHeader();

  const people = visiblePlayers(matches);
  const keys = S.VIEWS[activeView].keys;
  const perMatch = $("per-match").checked;

  // Include matches played as well as the visible statistics.
  // Values come from the current filtered totals/per-match view.
  const numericKeys = ["appearances", ...keys];
  const maxima = {};

  for (const key of numericKeys) {
    maxima[key] = null;

    for (const player of people) {
      const value = player[key];

      if (
        S.finite(value) &&
        (maxima[key] === null || value > maxima[key])
      ) {
        maxima[key] = value;
      }
    }
  }

  function isColumnMaximum(value, key) {
    return (
      S.finite(value) &&
      maxima[key] !== null &&
      value === maxima[key]
    );
  }

  const body = $("player-body");
  body.replaceChildren();

  $("view-note").textContent = S.VIEWS[activeView].note;
  $("view-count").textContent =
    `${people.length} players · ${perMatch ? "per available match" : "totals"}`;

  $("export-view").disabled = people.length === 0;

  if (!people.length) {
    const tr = node("tr");
    const td = node(
      "td",
      matches.length
        ? "No player records match this filter."
        : emptyText(),
      "empty"
    );

    td.colSpan = keys.length + 2;
    tr.append(td);
    body.append(tr);
    return;
  }

  for (const p of people) {
    const tr = node("tr");
    const first = node("th");
    const wrap = node("div", undefined, "player-cell");

    first.scope = "row";

    wrap.append(
      node("span", p.name.slice(0, 2).toUpperCase(), "avatar"),
      node("span", p.name, "player-name")
    );

    first.append(wrap);

    const appearances = node(
      "td",
      format(p.appearances),
      isColumnMaximum(p.appearances, "appearances")
        ? "column-best"
        : ""
    );

    appearances.dataset.stat = "appearances";

    tr.append(first, appearances);

    for (const key of keys) {
      const count = p.counts[key] || 0;
      const partial = S.finite(p[key]) && count < p.appearances;

      const td = node(
        "td",
        metricText(p[key], key, perMatch) + (partial ? " †" : ""),
        isColumnMaximum(p[key], key) ? "column-best" : ""
      );

      td.dataset.stat = key;
      td.title =
        `${count} / ${p.appearances} appearances contain valid data for this statistic.`;

      if (p.pairs[key]) {
        td.title +=
          ` ${p.pairs[key].made} completed / ${p.pairs[key].attempted} attempted.`;
      }

      if (
        S.METRICS[key].kind === "rate" &&
        !S.finite(p[key]) &&
        count
      ) {
        td.title += " No attempts: percentage is undefined, not 0%.";
      }

      tr.append(td);
    }

    body.append(tr);
  }
}
function miniTable(match, view) {
  const keys = S.VIEWS[view].keys, wrap = node("div", undefined, "table-wrap"), table = node("table"), thead = node("thead"), head = node("tr"), body = node("tbody");
  for (const key of ["name", ...keys]) {
    const def = S.METRICS[key], th = node("th", key === "name" ? "Player" : (key === "rating" ? "Rating" : def.label));
    th.scope = "col"; if (def) th.title = def.description; head.append(th);
  }
  thead.append(head); table.append(thead, body); wrap.append(table);
  for (const p of match.players || []) {
    const tr = node("tr"), th = node("th", p.name); th.scope = "row"; tr.append(th);
    for (const key of keys) tr.append(node("td", metricText(S.singleValue(p, key), key)));
    body.append(tr);
  }
  if (!(match.players || []).length) {
    const tr = node("tr"), td = node("td", "EA did not provide usable player records for this match.", "empty"); td.colSpan = keys.length + 1; tr.append(td); body.append(tr);
  }
  return wrap;
}
function renderMatches(matches) {
  const list = $("match-list"), opened = new Set([...list.querySelectorAll(".match-item[open]")].map(el => el.dataset.matchId));
  list.replaceChildren(); $("matches-label").textContent = `${matches.length} matches · most recent first`;
  if (!matches.length) list.append(node("div", emptyText(), "empty"));
  for (const m of matches.slice(0, matchLimit)) {
    const detail = node("details", undefined, "match-item"), summary = node("summary"); detail.dataset.matchId = m.id;
    summary.append(node("span", m.result || "?", `result ${["W", "D", "L"].includes(m.result) ? m.result : ""}`), node("span", dateText(m.timestamp), "match-time"),
      node("span", `vs ${m.opponent}`, "opponent"), node("span", `${format(m.goals)}:${format(m.goals_against)}`, "score"), node("span", m.match_types.map(t => labels[t] || t).join(" / "), "type-label"));
    detail.append(summary);
    let loaded = false;
    const expand = () => {
      if (!detail.open || loaded) return;
      loaded = true;
      const groups = node("div", undefined, "match-groups");
      for (const [key, view] of Object.entries(S.VIEWS)) {
        const section = node("details", undefined, "match-group"); section.dataset.group = key; section.open = key === "overview";
        section.append(node("summary", view.label), miniTable(m, key)); groups.append(section);
      }
      detail.append(groups, node("p", `Match ${m.id} · Actual match counts, regardless of the squad's per-match toggle. Outcome: ${m.result_source || "unavailable"}.`, "match-note"));
    };
    detail.addEventListener("toggle", expand);
    list.append(detail);
    if (opened.has(m.id)) {detail.open = true; expand();}
  }
  $("more-matches").hidden = matches.length <= matchLimit;
}
function render() {
  const matches = filteredMatches(), players = matches.flatMap(m => m.players || []), scored = matches.filter(m => S.finite(m.goals) && S.finite(m.goals_against));
  $("match-count").textContent = format(matches.length);
  $("match-caption").textContent = `${new Set(players.map(p => p.id)).size} players in these matches`;
  const wins = matches.filter(m => m.result === "W").length;
  const draws = matches.filter(m => m.result === "D").length;
  const losses = matches.filter(m => m.result === "L").length;

  const record = $("record");
  record.replaceChildren();

  if (matches.length) {
    record.append(
      node("span", String(wins), "record-win"),
      node("span", " · ", "record-separator"),
      node("span", String(draws), "record-draw"),
      node("span", " · ", "record-separator"),
      node("span", String(losses), "record-loss")
    );
  } else {
    record.textContent = "—";
  }

  $("record").title = `${matches.filter(m => !m.result).length} matches have unknown outcomes. Score fallbacks do not infer shoot-outs.`;
  const goalsFor = scored.reduce(
    (total, match) => total + match.goals,
    0
  );

  const goalsAgainst = scored.reduce(
    (total, match) => total + match.goals_against,
    0
  );

  const goals = $("goals");
  goals.replaceChildren();

  if (scored.length) {
    goals.append(
      node("span", String(goalsFor), "goals-for"),
      node("span", " / ", "record-separator"),
      node("span", String(goalsAgainst), "goals-against")
    );
  } else {
    goals.textContent = "—";
  }

  $("goal-caption").textContent = `${scored.length} / ${matches.length} matches with both score fields`;
  renderPlayers(matches); renderMatches(matches);
}
function selectView(key, focus = false) {
  if (!S.VIEWS[key]) return;
  activeView = key; sortKey = S.VIEWS[key].defaultSort; sortDirection = -1;
  for (const button of document.querySelectorAll("[data-view]")) {
    const selected = button.dataset.view === key;
    button.setAttribute("aria-selected", String(selected)); button.tabIndex = selected ? 0 : -1;
    if (selected && focus) button.focus();
  }
  $("stats-panel").setAttribute("aria-labelledby", `tab-${key}`);
  renderPlayers(filteredMatches());
}
function exportCurrentView() {
  const perMatch = $("per-match").checked;
  const csv = S.tableCsv(visiblePlayers(filteredMatches()), activeView, {perMatch, club});
  const blob = new Blob([csv], {type: "text/csv;charset=utf-8"});
  const url = URL.createObjectURL(blob), link = node("a");
  link.href = url; link.download = `${club ? C.fileSlug(club) : "club"}-${activeView}-${perMatch ? "per-match" : "totals"}.csv`;
  document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function renderIdentity() {
  $("club-name").textContent = club ? club.club_name : "Your club";
  $("edition").textContent = club ? `${club.edition.toUpperCase()} / ${club.platform.toUpperCase()} / CLUB ${club.club_id}` : "EA CLUBS / CLUB NOT CONFIGURED";
}
function renderStatus() {
  const ready = phase === "ready";
  // Keep synthetic payloads visibly labelled even if copied to another path.
  const synthetic = demo || archive.collection?.status === "demo";
  $("demo-banner").hidden = !synthetic;
  const report = archive.collection || {}, pill = $("status-pill"), status = report.status || "unknown", halted = archive.persistent_state?.halted;
  const needsNotice = ready && !synthetic && (report.needs_attention || halted || ["error", "partial", "blocked", "cooldown", "skipped"].includes(status));
  $("collector-health").hidden = !needsNotice;
  pill.textContent = needsNotice ? (halted ? "Paused: access denied" : status.replaceAll("_", " ")) : "";
  pill.className = "pill" + (status === "success" && !halted ? " good" : report.needs_attention || halted ? " bad" : "");
  $("health-text").textContent = needsNotice ? (report.message || "Check the collector log.") : "";
  $("freshness").textContent = report.last_api_attempt_at ? "API attempt: " + dateText(report.last_api_attempt_at) : "";
  $("archive-stamp").textContent = !ready ? "" : synthetic ? "Synthetic data · not your club records"
    : archive.archive_updated_at ? `${club.club_name} archive changed: ${dateText(archive.archive_updated_at)}` : `${club.club_name} archive is empty`;
  const log = $("log-link"), repository = archive.repository;
  log.hidden = !(ready && repository && /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository));
  if (!log.hidden) log.href = `https://github.com/${repository}/actions/workflows/archive-and-publish.yml`;
  const all = $("export");
  all.hidden = !ready || synthetic;
  if (!all.hidden) {all.href = C.csvPath(catalog, club.club_id); all.download = `${C.fileSlug(club)}-player-matches.csv`;}
}
function renderFailure(error) {
  $("collector-health").hidden = false;
  $("status-pill").textContent = "Data unavailable";
  $("health-text").textContent = club ? `The viewer could not load the generated data for ${club.club_name}.` : "The viewer could not load its generated data.";
  const box = $("load-error"); box.hidden = false;
  box.textContent = error.message + " Run python scripts/tracker.py build, then preview with python -m http.server --directory site. On GitHub, inspect the Actions log.";
}
function renderAll() {
  renderIdentity(); renderStatus(); render();
}
function fillSelector() {
  const names = catalog.clubs.map(entry => entry.club_name);
  // Same-named clubs stay distinguishable by ID.
  $("club-select").replaceChildren(...catalog.clubs.map(entry => new Option(
    names.indexOf(entry.club_name) === names.lastIndexOf(entry.club_name) ? entry.club_name : `${entry.club_name} (${entry.club_id})`, entry.club_id)));
  $("club-picker").hidden = catalog.clubs.length < 2;
}
function reconcileFilters() {
  // The competition and player search depend on the club just loaded; a value
  // that selects nothing there would look like an empty archive.
  const competition = $("competition"), search = $("player-search");
  if (competition.value !== "all" && !archive.matches.some(m => (m.match_types || []).includes(competition.value))) competition.value = "all";
  const query = search.value.trim().toLocaleLowerCase();
  if (query && !archive.matches.some(m => (m.players || []).some(p => String(p.name || "").toLocaleLowerCase().includes(query)))) search.value = "";
}
function checkDataset(data, expected) {
  if (!data || ![1, 2].includes(data.schema_version) || !Array.isArray(data.matches)) throw new Error("Unexpected viewer data format.");
  // A dataset served from one club's path must say it belongs to that club.
  const owner = data.club || data.config || {};
  if (String(owner.club_id) !== expected.club_id) throw new Error(`The published data does not belong to ${expected.club_name}.`);
}
async function getJson(url, signal) {
  const response = await fetch(url, {cache: "no-store", signal});
  if (!response.ok) throw new Error(`Data request returned HTTP ${response.status}.`);
  return response.json();
}
async function showClub(next, {remember = false, address = false} = {}) {
  const run = ++loadRun;
  if (loadAbort) loadAbort.abort();
  loadAbort = new AbortController();
  // Blank the page before fetching: the previous club's numbers, open match
  // details and errors must not sit under the new club's name.
  club = next; archive = NO_DATA; phase = "loading"; matchLimit = 20;
  $("match-list").replaceChildren(); $("load-error").hidden = true;
  $("club-select").value = club.club_id;
  if (remember) try {localStorage.setItem(STORAGE_KEY, club.club_id);} catch {}
  if (address) try {
    const url = new URL(location.href); url.searchParams.set("club", club.club_id);
    history.replaceState(null, "", url);
  } catch {}
  renderAll();
  let data = null, failure = null;
  try {
    data = await getJson(C.dataPath(catalog, next.club_id), loadAbort.signal);
    checkDataset(data, next);
  } catch (error) {
    failure = error;
  }
  // A newer selection owns the page now; this late answer is dropped.
  if (run !== loadRun) return;
  if (failure) {phase = "failed"; renderAll(); renderFailure(failure); return;}
  archive = data; phase = "ready";
  reconcileFilters(); renderAll();
}
function wire() {
  $("club-select").addEventListener("change", event => {
    const next = C.findClub(catalog, event.target.value);
    if (next) showClub(next, {remember: true, address: true});
  });
  for (const id of ["period", "competition", "per-match"]) $(id).addEventListener("change", () => {matchLimit = 20; render();});
  $("player-search").addEventListener("input", () => renderPlayers(filteredMatches()));
  $("player-head").addEventListener("click", event => {
    const button = event.target.closest("button[data-sort]"); if (!button) return;
    const key = button.dataset.sort; sortDirection = key === sortKey ? -sortDirection : key === "name" ? 1 : -1; sortKey = key;
    renderPlayers(filteredMatches());
  });
  document.querySelectorAll("[data-view]").forEach(button => button.addEventListener("click", () => selectView(button.dataset.view)));
  $("stat-tabs").addEventListener("keydown", event => {
    const keys = Object.keys(S.VIEWS), index = keys.indexOf(activeView);
    const next = {ArrowRight: (index + 1) % keys.length, ArrowLeft: (index + keys.length - 1) % keys.length, Home: 0, End: keys.length - 1}[event.key];
    if (next !== undefined) {event.preventDefault(); selectView(keys[next], true);}
  });
  $("more-matches").addEventListener("click", () => {matchLimit += 20; renderMatches(filteredMatches());});
  $("export-view").addEventListener("click", exportCurrentView);
}
async function init() {
  wire();
  const params = new URLSearchParams(location.search);
  demo = params.get("demo") === "1";
  $("demo-banner").hidden = !demo;
  try {
    if (demo) {
      // The demo is one synthetic club in one file; it has no catalog to switch in.
      const data = await getJson("demo.json");
      catalog = C.parseCatalog({clubs: [data.club || data.config]});
      club = catalog.clubs[0]; checkDataset(data, club);
      archive = data; phase = "ready";
      fillSelector(); renderAll();
      return;
    }
    catalog = C.parseCatalog(await getJson("data/clubs.json"));
  } catch (error) {
    phase = "failed"; renderAll(); renderFailure(error);
    return;
  }
  fillSelector();
  if (!catalog.clubs.length) {phase = "setup"; renderAll(); return;}
  let remembered = null;
  try {remembered = localStorage.getItem(STORAGE_KEY);} catch {}
  await showClub(C.resolveClub(catalog, {requested: params.get("club"), remembered}));
}
init();
