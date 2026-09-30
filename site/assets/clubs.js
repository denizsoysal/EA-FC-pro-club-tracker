/* Club catalog and selection rules shared by the viewer and Node's test runner.
 * No dependencies. A club ID reaches a URL or filename only after it has matched
 * an entry of the published catalog; nothing is built from raw address-bar text.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ClubCatalog = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const CLUB_ID = /^[0-9]{1,20}$/;
  const text = value => typeof value === "string" ? value : "";
  function parseCatalog(value) {
    if (!value || typeof value !== "object" || !Array.isArray(value.clubs)) throw new Error("Unexpected club catalog format.");
    const clubs = [], seen = new Set();
    for (const entry of value.clubs) {
      const id = text(entry && entry.club_id);
      if (!CLUB_ID.test(id) || seen.has(id)) throw new Error("Unexpected club catalog format.");
      seen.add(id);
      clubs.push({club_id: id, club_name: text(entry.club_name).trim() || `Club ${id}`,
        edition: text(entry.edition), platform: text(entry.platform)});
    }
    const wanted = text(value.default_club_id);
    return {default_club_id: seen.has(wanted) ? wanted : clubs.length ? clubs[0].club_id : "", clubs};
  }
  function findClub(catalog, id) {
    if (typeof id !== "string" || !CLUB_ID.test(id)) return null;
    return catalog.clubs.find(club => club.club_id === id) || null;
  }
  // A valid address-bar choice wins over the remembered one, then the default.
  function resolveClub(catalog, {requested = null, remembered = null} = {}) {
    return findClub(catalog, requested) || findClub(catalog, remembered)
      || findClub(catalog, catalog.default_club_id) || catalog.clubs[0] || null;
  }
  function configured(catalog, id) {
    const club = findClub(catalog, id);
    if (!club) throw new Error("That club is not in the published catalog.");
    return club;
  }
  // Relative on purpose: the site is served from a repository subpath.
  function dataPath(catalog, id) {
    return `data/clubs/${configured(catalog, id).club_id}/index.json`;
  }
  function csvPath(catalog, id) {
    const club = configured(catalog, id);
    return `data/clubs/${club.club_id}/player_matches_${club.club_id}.csv`;
  }
  function fileSlug(club) {
    const name = text(club.club_name).normalize("NFKD").replace(/\p{M}+/gu, "")
      .replace(/[^A-Za-z0-9]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase();
    return `${name || "club"}-${club.club_id}`;
  }
  return {CLUB_ID, parseCatalog, findClub, resolveClub, dataPath, csvPath, fileSlug};
});
