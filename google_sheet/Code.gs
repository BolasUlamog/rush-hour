/**
 * Gridlock Sprint scoreboard, kept in this spreadsheet.
 *
 * Paste this into Extensions > Apps Script on the sheet you want the scores in,
 * then Deploy > New deployment > Web app, with:
 *   Execute as:      Me
 *   Who has access:  Anyone
 * Copy the deployment URL into the grading station's SHEET_WEBHOOK_URL.
 *
 * "Anyone" means anyone with the URL can post, so set SHARED_TOKEN below to a
 * long random string and give the station the same value as SHEET_TOKEN. The
 * script runs as you, which is why no credentials ever reach the station.
 */

const SHARED_TOKEN = "change-me";      // must match SHEET_TOKEN in the app
const TAB = "Scores";                  // created by this script; your own tabs are left alone
const TEAMS_TAB = "TEAMS";             // where the acceptable team names live
// Labels that sit among the team names and are not teams.
const NOT_A_TEAM = ["TEAM", "TEAMS", "PERSON ID", "PERSON", "TIME", "POINT VALUE", "POINTS",
                    "CORRECT?", "CORRECT", "SCORE", "SCORING", "TOTAL", "NAME", "ID"];
const HEADERS = [
  "contest", "team", "puzzle", "points possible", "points awarded", "result",
  "moves used", "optimal", "moves", "graded by", "graded at",
];

function sheet_() {
  const book = SpreadsheetApp.getActiveSpreadsheet();
  let tab = book.getSheetByName(TAB);
  if (!tab) {
    tab = book.insertSheet(TAB);
  }
  if (tab.getLastRow() === 0) {
    tab.appendRow(HEADERS);
    tab.setFrozenRows(1);
  }
  return tab;
}

function rowsOf_(tab) {
  const last = tab.getLastRow();
  if (last < 2) return [];
  return tab.getRange(2, 1, last - 1, HEADERS.length).getValues();
}

/**
 * The acceptable team names, read from the TEAMS tab.
 *
 * The grading station uses these to correct a misread team ID: the list is
 * closed, so an ID that is not on it is certainly wrong. Names are looked for
 * anywhere on the tab rather than in a fixed range, because the sheet's layout
 * is still being worked out — down a column or across a row both work.
 */
function teams_() {
  const book = SpreadsheetApp.getActiveSpreadsheet();
  const tab = book.getSheetByName(TEAMS_TAB);
  if (!tab || tab.getLastRow() === 0) return [];
  const values = tab.getRange(1, 1, tab.getLastRow(), tab.getLastColumn()).getValues();
  const found = [];
  values.forEach(row => row.forEach(cell => {
    const name = String(cell || "").trim().toUpperCase();
    if (!name) return;
    if (NOT_A_TEAM.indexOf(name) >= 0) return;
    if (!/^[A-Z][A-Z0-9 \-]{2,19}$/.test(name)) return;
    if (found.indexOf(name) < 0) found.push(name);
  }));
  return found.sort();
}

function reply_(value) {
  return ContentService.createTextOutput(JSON.stringify(value))
    .setMimeType(ContentService.MimeType.JSON);
}

/** One row per contest+team+puzzle: grading a sheet again replaces its score. */
function save_(tab, body) {
  const key = [String(body.contest), String(body.team), String(body.puzzle)];
  const row = [
    key[0], key[1], key[2],
    Number(body.pointsPossible || 0), Number(body.pointsAwarded || 0),
    String(body.status || ""), Number(body.movesUsed || 0),
    body.optimal === null || body.optimal === undefined ? "" : Number(body.optimal),
    String(body.moves || ""), String(body.gradedBy || ""), new Date().toISOString(),
  ];
  const existing = rowsOf_(tab);
  for (let i = 0; i < existing.length; i++) {
    if (String(existing[i][0]) === key[0] && String(existing[i][1]) === key[1]
        && String(existing[i][2]) === key[2]) {
      tab.getRange(i + 2, 1, 1, HEADERS.length).setValues([row]);
      return { ok: true, replaced: true };
    }
  }
  tab.appendRow(row);
  return { ok: true, replaced: false };
}

function standings_(tab, contest) {
  const entries = [];
  const totals = {};
  rowsOf_(tab).forEach(row => {
    if (String(row[0]) !== String(contest)) return;
    const entry = {
      team: String(row[1]), puzzle: String(row[2]),
      pointsPossible: Number(row[3]) || 0, pointsAwarded: Number(row[4]) || 0,
      status: String(row[5]), movesUsed: Number(row[6]) || null,
      optimal: row[7] === "" ? null : Number(row[7]),
      moves: String(row[8]), gradedBy: String(row[9]), gradedAt: String(row[10]),
    };
    entries.push(entry);
    const team = totals[entry.team] || { team: entry.team, points: 0, sheets: 0, solved: 0 };
    team.points += entry.pointsAwarded;
    team.sheets += 1;
    team.solved += entry.pointsAwarded > 0 ? 1 : 0;
    totals[entry.team] = team;
  });
  const teams = Object.keys(totals).map(name => totals[name])
    .sort((a, b) => b.points - a.points || a.team.localeCompare(b.team));
  return { ok: true, contest: String(contest), teams: teams, entries: entries, shared: true };
}

function clear_(tab, contest) {
  const rows = rowsOf_(tab);
  let removed = 0;
  // Delete from the bottom up so the indexes above stay valid.
  for (let i = rows.length - 1; i >= 0; i--) {
    if (String(rows[i][0]) === String(contest)) {
      tab.deleteRow(i + 2);
      removed += 1;
    }
  }
  return { ok: true, cleared: removed };
}

function handle_(body) {
  if (SHARED_TOKEN && String(body.token || "") !== SHARED_TOKEN) {
    return reply_({ ok: false, error: "Wrong or missing token." });
  }
  // One writer at a time: two volunteers can press Grade at the same moment.
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const tab = sheet_();
    const action = String(body.action || "standings");
    if (action === "save") return reply_(save_(tab, body));
    if (action === "clear") return reply_(clear_(tab, String(body.contest || "")));
    if (action === "standings") {
      const answer = standings_(tab, String(body.contest || ""));
      answer.teamNames = teams_();
      return reply_(answer);
    }
    if (action === "teams") return reply_({ ok: true, teamNames: teams_() });
    return reply_({ ok: false, error: "Unknown action " + action });
  } finally {
    lock.releaseLock();
  }
}

function doPost(event) {
  try {
    return handle_(JSON.parse((event.postData && event.postData.contents) || "{}"));
  } catch (error) {
    return reply_({ ok: false, error: String(error) });
  }
}

function doGet(event) {
  // Handy for checking the deployment from a browser.
  const parameters = (event && event.parameter) || {};
  return handle_({ token: parameters.token, action: "standings", contest: parameters.contest || "" });
}
