(function () {
  "use strict";
  /**
   * Answer-sheet station.
   *
   * A sheet is self-contained: the QR square on the page carries the board, the
   * answer-row count and the points. So this page needs no packet list and no
   * stored state. It sends the photo to be read, solves the decoded board here
   * with engine.js to find the shortest route, then checks what the student
   * wrote and awards the page's points if the route frees the X car.
   */
  const engine = window.RushHour;
  const MAX_PIXELS = 3000;
  const STORE_KEY = "gridlock.tally.v2";          // only used when there is no shared database
  const SETTINGS_KEY = "gridlock.settings.v1";
  const REFRESH_MS = 8000;

  const views = {
    sheets: document.getElementById("sheetsView"),
    make: document.getElementById("makeView")
  };
  const tabs = {
    sheets: document.getElementById("tabSheets"),
    make: document.getElementById("tabMake")
  };
  const photoInput = document.getElementById("sheetPhoto");
  const photoPreview = document.getElementById("sheetPreview");
  const uploadTitle = document.getElementById("sheetUploadTitle");
  const uploadNote = document.getElementById("sheetUploadNote");
  const uploadDefaults = { title: uploadTitle.textContent, note: uploadNote.textContent };
  const uploadBox = document.getElementById("sheetUploadBox");
  const scanButton = document.getElementById("sheetScanButton");
  const statusBadge = document.getElementById("scanStatusBadge");
  const resultBox = document.getElementById("sheetResult");
  const readout = document.getElementById("sheetReadout");
  const teamInput = document.getElementById("sheetTeam");
  const codeLabel = document.getElementById("sheetCode");
  const optimalLabel = document.getElementById("sheetOptimal");
  const pointsLabel = document.getElementById("sheetPoints");
  const movesInput = document.getElementById("sheetMoves");
  const confidenceBadge = document.getElementById("sheetConfidence");
  const warningList = document.getElementById("sheetWarnings");
  const gradeButton = document.getElementById("sheetGradeButton");
  const skipButton = document.getElementById("sheetSkipButton");
  const tallyTable = document.getElementById("tallyTable");
  const exportButton = document.getElementById("exportButton");
  const resetTallyButton = document.getElementById("resetTallyButton");
  const contestInput = document.getElementById("contestInput");
  const graderInput = document.getElementById("graderInput");
  const scoreboardNote = document.getElementById("scoreboardNote");

  let sheetImage = null;
  let current = null;      // the puzzle decoded from the sheet being graded
  let tally = {};          // the local fallback, when no database is configured
  let standings = null;    // what the server last told us
  let shared = false;
  let teamNames = [];      // the acceptable team names, when the sheet supplies them

  function setMode(mode) {
    Object.keys(views).forEach(name => {
      views[name].hidden = name !== mode;
      tabs[name].classList.toggle("is-active", name === mode);
      tabs[name].setAttribute("aria-selected", String(name === mode));
    });
  }

  function setResult(status, title, detail) {
    const icons = { neutral: "•", success: "✓", error: "!", partial: "→" };
    resultBox.className = `result ${status}`;
    resultBox.replaceChildren();
    const icon = document.createElement("span");
    icon.className = "result-icon";
    icon.textContent = icons[status];
    const copy = document.createElement("div");
    const heading = document.createElement("strong");
    const description = document.createElement("p");
    heading.textContent = title;
    description.textContent = detail;
    copy.append(heading, description);
    resultBox.append(icon, copy);
  }

  function contest() {
    return (contestInput.value || "R1").trim().toUpperCase() || "R1";
  }

  function saveSettings() {
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify({
        contest: contestInput.value, grader: graderInput.value
      }));
    } catch (error) { /* a refused localStorage only costs convenience here */ }
  }

  function loadSettings() {
    try {
      const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
      if (saved.contest) contestInput.value = saved.contest;
      if (saved.grader) graderInput.value = saved.grader;
    } catch (error) { /* ignore */ }
  }

  function loadLocalTally() {
    try {
      tally = JSON.parse(localStorage.getItem(STORE_KEY) || "{}");
    } catch (error) {
      tally = {};
    }
  }

  function saveLocalTally() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(tally));
    } catch (error) {
      setResult("partial", "Scores could not be saved",
        "This browser refused local storage, so the tally will be lost on reload. Export the CSV often.");
    }
  }

  /** Totals from the local fallback, shaped like the server's reply. */
  function localStandings() {
    const totals = new Map();
    const entries = [];
    Object.values(tally).forEach(entry => {
      entries.push(entry);
      const row = totals.get(entry.team) || { team: entry.team, points: 0, sheets: 0, solved: 0 };
      row.points += entry.pointsAwarded;
      row.sheets += 1;
      row.solved += entry.pointsAwarded > 0 ? 1 : 0;
      totals.set(entry.team, row);
    });
    return {
      contest: contest(),
      teams: [...totals.values()].sort((a, b) => b.points - a.points || a.team.localeCompare(b.team)),
      entries,
      shared: false
    };
  }

  async function refreshStandings() {
    try {
      const response = await fetch(`/api/scores?contest=${encodeURIComponent(contest())}`);
      if (!response.ok) throw new Error("no scoreboard");
      standings = await response.json();
      shared = Boolean(standings.shared);
      if (Array.isArray(standings.teamNames)) {
        teamNames = standings.teamNames;
        fillTeamOptions();
      }
    } catch (error) {
      // No server or no database: fall back to this browser's own tally.
      loadLocalTally();
      standings = localStandings();
      shared = false;
    }
    renderTally();
  }

  /** Offer the tournament's team names as autocomplete on the team field. */
  function fillTeamOptions() {
    let list = document.getElementById("teamNameList");
    if (!list) {
      list = document.createElement("datalist");
      list.id = "teamNameList";
      document.body.append(list);
      teamInput.setAttribute("list", "teamNameList");
    }
    list.replaceChildren(...teamNames.map(name => {
      const option = document.createElement("option");
      option.value = name;
      return option;
    }));
  }

  function renderTally() {
    const data = standings || localStandings();
    const teams = data.teams || [];
    document.getElementById("tallyTeams").textContent = teams.length;
    document.getElementById("tallySheets").textContent = (data.entries || []).length;
    document.getElementById("tallyPoints").textContent = teams.reduce((sum, row) => sum + row.points, 0);

    scoreboardNote.textContent = shared
      ? "Shared scoreboard: every volunteer grading this round writes here, and this list refreshes on its own."
      : "No shared database, so these scores are only on this device. Several volunteers would each keep their own.";
    scoreboardNote.className = shared ? "notation" : "notation warn";

    tallyTable.replaceChildren();
    if (!teams.length) {
      const empty = document.createElement("p");
      empty.className = "tally-empty";
      empty.textContent = "No sheets graded yet.";
      tallyTable.append(empty);
      return;
    }
    const table = document.createElement("table");
    const head = document.createElement("thead");
    head.innerHTML = "<tr><th>Team</th><th>Solved</th><th>Sheets</th><th>Points</th></tr>";
    const body = document.createElement("tbody");
    teams.forEach(row => {
      const tr = document.createElement("tr");
      [row.team, row.solved, row.sheets, row.points].forEach((value, index) => {
        const cell = document.createElement(index === 0 ? "th" : "td");
        cell.textContent = value;
        tr.append(cell);
      });
      body.append(tr);
    });
    table.append(head, body);
    tallyTable.append(table);
  }

  function readFile(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(new Error("Could not read that file."));
      reader.onload = () => resolve(reader.result);
      reader.readAsDataURL(file);
    });
  }

  function shrinkToJpeg(dataUrl) {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.onerror = () => reject(new Error("This browser cannot decode that photo."));
      image.onload = () => {
        const scale = Math.min(1, MAX_PIXELS / Math.max(image.width, image.height));
        const canvas = document.createElement("canvas");
        canvas.width = Math.round(image.width * scale);
        canvas.height = Math.round(image.height * scale);
        canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
        resolve(canvas.toDataURL("image/jpeg", .92));
      };
      image.src = dataUrl;
    });
  }

  /**
   * Prepare a photo for upload.
   *
   * Shrinking it here keeps the upload small, but browsers disagree about
   * decoding HEIC, so when that fails the original file is sent as it is and the
   * server decodes it. Either way the grader is not made to convert anything.
   */
  async function prepareSheet(file) {
    const raw = await readFile(file);
    try {
      const jpeg = await shrinkToJpeg(raw);
      return { image: jpeg, preview: jpeg };
    } catch (error) {
      return { image: raw, preview: null };
    }
  }

  /** Grow the move box to the answer: a grandmaster sheet can hold 40 rows. */
  function fitMoves() {
    const lines = movesInput.value.split("\n").length;
    movesInput.rows = Math.max(6, Math.min(24, lines + 1));
  }

  function editDistance(left, right) {
    let previous = Array.from({ length: right.length + 1 }, (unused, i) => i);
    for (let i = 1; i <= left.length; i++) {
      const current = [i];
      for (let j = 1; j <= right.length; j++) {
        current[j] = Math.min(previous[j] + 1, current[j - 1] + 1,
                              previous[j - 1] + (left[i - 1] === right[j - 1] ? 0 : 1));
      }
      previous = current;
    }
    return previous[right.length];
  }

  /**
   * Correct a scanned team ID against the tournament's list of team names.
   *
   * A team ID has no restricted alphabet of its own, so B/D/R and 1/I are
   * genuinely hard to tell apart, and a misread hands a real team's points to a
   * team that does not exist. The list of acceptable names is closed, though, so
   * an ID that is not on it is certainly wrong and usually one letter away from
   * the right answer.
   *
   * A single close name is used, and always reported. Anything ambiguous is left
   * exactly as read for the grader to settle.
   */
  function matchTeam(read) {
    if (!read || !teamNames.length) return { team: read, note: "" };
    if (teamNames.includes(read)) return { team: read, note: "" };
    const scored = teamNames
      .map(name => ({ name, distance: editDistance(read, name) }))
      .sort((a, b) => a.distance - b.distance);
    const best = scored[0];
    const limit = read.length <= 4 ? 1 : 2;
    if (!best || best.distance > limit) {
      return {
        team: read,
        note: `“${read}” is not one of the ${teamNames.length} team names. Check the sheet.`
      };
    }
    const tied = scored.filter(row => row.distance === best.distance);
    if (tied.length > 1) {
      return {
        team: read,
        note: `“${read}” is not a team name; it could be ` +
              `${tied.slice(0, 3).map(row => row.name).join(" or ")}. Check the sheet.`
      };
    }
    return {
      team: best.name,
      note: `Team read as “${read}” and corrected to “${best.name}”, the only team name that close. ` +
            "Check the sheet before saving."
    };
  }

  function showWarnings(warnings) {
    warningList.replaceChildren();
    warnings.forEach(text => {
      const item = document.createElement("li");
      item.textContent = text;
      warningList.append(item);
    });
    warningList.hidden = !warnings.length;
  }

  photoInput.addEventListener("change", async () => {
    const file = photoInput.files && photoInput.files[0];
    if (!file) return;
    try {
      const prepared = await prepareSheet(file);
      sheetImage = prepared.image;
      if (prepared.preview) {
        photoPreview.src = prepared.preview;
        uploadBox.classList.add("has-photo");
        uploadTitle.textContent = uploadDefaults.title;
        uploadNote.textContent = uploadDefaults.note;
      } else {
        photoPreview.removeAttribute("src");
        uploadBox.classList.remove("has-photo");
        uploadTitle.textContent = file.name || "Photo ready";
        uploadNote.textContent = "This browser cannot preview that format, so it will be read on the Mac instead.";
      }
      scanButton.disabled = false;
      setResult("neutral", "Sheet loaded", "Read it, then check every row against the paper.");
    } catch (error) {
      setResult("error", "Could not load that photo", error.message);
    }
  });

  scanButton.addEventListener("click", async () => {
    if (!sheetImage) return;
    scanButton.disabled = true;
    scanButton.firstChild.textContent = "Reading the sheet… ";
    statusBadge.textContent = "Reading";
    try {
      const response = await fetch("/api/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image: sheetImage })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "The sheet could not be read.");

      // The board came off the paper; work out the shortest route here.
      const optimal = engine.solve(data.cars);
      current = { ...data, optimal };

      readout.hidden = false;
      teamInput.value = data.team || "";
      codeLabel.textContent = data.puzzleCode;
      pointsLabel.textContent = `${data.points} pts`;
      optimalLabel.textContent = optimal ? `${optimal.length} moves` : "unknown";
      movesInput.value = data.moves.join("\n");
      fitMoves();

      const percent = Math.round((data.confidence || 0) * 100);
      const clean = !data.warnings.length;
      confidenceBadge.className = clean ? "good" : "review";
      confidenceBadge.textContent = clean ? `${percent}% clear` : `${percent}% · review`;
      const matched = matchTeam(data.team);
      teamInput.value = matched.team;
      showWarnings(matched.note ? [matched.note, ...data.warnings] : data.warnings);
      statusBadge.textContent = data.puzzleCode;

      const rowsRead = data.rows.filter(row => row.move).length;
      if (clean) {
        setResult("success", `Read ${data.puzzleCode}`,
          `${rowsRead} move${rowsRead === 1 ? "" : "s"} for team ${data.team || "—"}. Confirm and grade.`);
      } else {
        setResult("partial", `Read ${data.puzzleCode} with questions`,
          `${data.warnings.length} thing${data.warnings.length === 1 ? "" : "s"} to check against the paper.`);
      }
      movesInput.focus();
    } catch (error) {
      statusBadge.textContent = "Not read";
      setResult("error", "Could not read that sheet", error.message);
    } finally {
      scanButton.disabled = false;
      scanButton.firstChild.textContent = "Read this sheet ";
    }
  });

  gradeButton.addEventListener("click", async () => {
    if (readout.hidden || !current) return;
    const team = teamInput.value.trim().toUpperCase();
    if (!team) {
      setResult("error", "Team ID is required", "Type the team ID from the top of the sheet.");
      teamInput.focus();
      return;
    }
    const outcome = engine.validateSolution(current.cars, movesInput.value);
    const awarded = outcome.status === "success" ? current.points : 0;
    const moves = movesInput.value.trim().split(/\s*\n\s*/).filter(Boolean);
    const entry = {
      contest: contest(),
      team,
      puzzle: current.puzzleCode,
      pointsPossible: current.points,
      pointsAwarded: awarded,
      status: outcome.status,
      moves: moves.join(" "),
      movesUsed: moves.length,
      optimal: current.optimal ? current.optimal.length : null,
      gradedBy: (graderInput.value || "").trim() || null
    };

    const efficiency = current.optimal && moves.length === current.optimal.length
      ? " That is a shortest route."
      : current.optimal ? ` Shortest possible is ${current.optimal.length}.` : "";
    if (awarded) {
      setResult("success", `${team} earns ${awarded} points`, `${outcome.message}${efficiency}`);
    } else {
      setResult("error", `${team} earns 0 points on ${current.puzzleCode}`, outcome.message);
    }
    clearSheet(false);

    // Saving replaces any earlier score for this team and puzzle, so a retry
    // after a dropped connection cannot double-count.
    try {
      const response = await fetch("/api/score", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(entry)
      });
      if (!response.ok) throw new Error((await response.json()).error || "could not save");
      standings = await response.json();
      shared = Boolean(standings.shared);
      renderTally();
    } catch (error) {
      loadLocalTally();
      tally[`${entry.contest}|${team}|${entry.puzzle}`] = entry;
      saveLocalTally();
      standings = localStandings();
      shared = false;
      renderTally();
      setResult("partial", `${team}: ${awarded} points, saved on this device only`,
        `The scoreboard could not be reached (${error.message}), so this score is local. Export the CSV before closing.`);
    }
    clearSheet(false);
  });

  function clearSheet(resetResult) {
    sheetImage = null;
    current = null;
    photoInput.value = "";
    photoPreview.removeAttribute("src");
    uploadBox.classList.remove("has-photo");
    uploadTitle.textContent = uploadDefaults.title;
    uploadNote.textContent = uploadDefaults.note;
    scanButton.disabled = true;
    readout.hidden = true;
    movesInput.value = "";
    movesInput.rows = 6;
    teamInput.value = "";
    showWarnings([]);
    confidenceBadge.className = "";
    confidenceBadge.textContent = "—";
    if (resetResult) {
      statusBadge.textContent = "Ready";
      setResult("neutral", "Ready for the next sheet", "Photograph the whole page, corner squares included.");
    }
  }

  skipButton.addEventListener("click", () => clearSheet(true));

  exportButton.addEventListener("click", () => {
    const data = standings || localStandings();
    if (!(data.entries || []).length) {
      setResult("partial", "Nothing to export yet", "Grade at least one sheet first.");
      return;
    }
    const quote = value => `"${String(value === null || value === undefined ? "" : value).replace(/"/g, '""')}"`;
    const rows = [[`Gridlock Sprint ${data.contest} team totals`].map(quote).join(",")];
    rows.push(["team", "points", "puzzles solved", "sheets graded"].map(quote).join(","));
    data.teams.forEach(row => rows.push([row.team, row.points, row.solved, row.sheets].map(quote).join(",")));
    rows.push("");
    rows.push(["team", "puzzle", "points possible", "points awarded", "result",
               "moves used", "optimal moves", "moves", "graded by", "graded at"].map(quote).join(","));
    [...data.entries]
      .sort((a, b) => a.team.localeCompare(b.team) || a.puzzle.localeCompare(b.puzzle))
      .forEach(entry => rows.push([
        entry.team, entry.puzzle, entry.pointsPossible, entry.pointsAwarded, entry.status,
        entry.movesUsed, entry.optimal, entry.moves, entry.gradedBy, entry.gradedAt
      ].map(quote).join(",")));

    const blob = new Blob([rows.join("\n")], { type: "text/csv;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `gridlock-${data.contest}-${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  });

  resetTallyButton.addEventListener("click", async () => {
    const data = standings || localStandings();
    const points = (data.teams || []).reduce((sum, row) => sum + row.points, 0);
    if (!(data.entries || []).length) return;
    const where = shared ? "for everyone grading this round" : "on this device";
    if (!confirm(`Delete every score in round ${contest()} ${where}? ` +
                 `${points} points across ${data.teams.length} teams will be lost.`)) return;
    try {
      const response = await fetch("/api/scores/clear", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ contest: contest() })
      });
      if (!response.ok) throw new Error("could not clear");
    } catch (error) {
      tally = {};
      localStorage.removeItem(STORE_KEY);
    }
    await refreshStandings();
    setResult("neutral", `Round ${contest()} cleared`, "The scoreboard is empty again.");
  });

  tabs.sheets.addEventListener("click", () => setMode("sheets"));
  tabs.make.addEventListener("click", () => setMode("make"));
  movesInput.addEventListener("input", fitMoves);
  movesInput.addEventListener("keydown", event => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") gradeButton.click();
  });

  // A plain running clock, so a grader can see the session length.
  const started = performance.now();
  (function tick(now) {
    const elapsed = (now || performance.now()) - started;
    const minutes = Math.floor(elapsed / 60000);
    const seconds = Math.floor((elapsed % 60000) / 1000);
    document.getElementById("timer").textContent =
      `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${Math.floor((elapsed % 1000) / 100)}`;
    requestAnimationFrame(tick);
  })();

  contestInput.addEventListener("change", () => { saveSettings(); refreshStandings(); });
  graderInput.addEventListener("change", saveSettings);

  setMode("sheets");
  loadSettings();
  refreshStandings();
  // Other volunteers are grading at the same time, so keep the board current.
  setInterval(() => { if (!views.sheets.hidden) refreshStandings(); }, REFRESH_MS);
})();
