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
  const STORE_KEY = "gridlock.tally.v2";

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

  let sheetImage = null;
  let current = null;      // the puzzle decoded from the sheet being graded
  let tally = {};

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

  function loadTally() {
    try {
      tally = JSON.parse(localStorage.getItem(STORE_KEY) || "{}");
    } catch (error) {
      tally = {};
    }
    renderTally();
  }

  function saveTally() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(tally));
    } catch (error) {
      setResult("partial", "Scores could not be saved",
        "This browser refused local storage, so the tally will be lost on reload. Export the CSV often.");
    }
  }

  function teamTotals() {
    const totals = new Map();
    Object.values(tally).forEach(entry => {
      const row = totals.get(entry.team) || { team: entry.team, points: 0, sheets: 0, solved: 0 };
      row.points += entry.awarded;
      row.sheets += 1;
      row.solved += entry.awarded > 0 ? 1 : 0;
      totals.set(entry.team, row);
    });
    return [...totals.values()].sort((a, b) => b.points - a.points || a.team.localeCompare(b.team));
  }

  function renderTally() {
    const totals = teamTotals();
    document.getElementById("tallyTeams").textContent = totals.length;
    document.getElementById("tallySheets").textContent = Object.keys(tally).length;
    document.getElementById("tallyPoints").textContent = totals.reduce((sum, row) => sum + row.points, 0);

    tallyTable.replaceChildren();
    if (!totals.length) {
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
    totals.forEach(row => {
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
      const response = await fetch("/api/scan-sheet", {
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
      showWarnings(data.warnings);
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

  gradeButton.addEventListener("click", () => {
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
    const key = `${team}|${current.puzzleCode}`;
    const replaced = Object.prototype.hasOwnProperty.call(tally, key);
    tally[key] = {
      team,
      code: current.puzzleCode,
      possible: current.points,
      awarded,
      status: outcome.status,
      message: outcome.message,
      optimal: current.optimal ? current.optimal.length : null,
      used: moves.length,
      moves: moves.join(" "),
      at: new Date().toISOString()
    };
    saveTally();
    renderTally();

    const note = replaced ? " This replaced an earlier score for the same team and puzzle." : "";
    if (awarded) {
      const efficiency = current.optimal && moves.length === current.optimal.length
        ? " That is a shortest route."
        : current.optimal ? ` Shortest possible is ${current.optimal.length}.` : "";
      setResult("success", `${team} earns ${awarded} points`, `${outcome.message}${efficiency}${note}`);
    } else {
      setResult("error", `${team} earns 0 points on ${current.puzzleCode}`, `${outcome.message}${note}`);
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
    if (!Object.keys(tally).length) {
      setResult("partial", "Nothing to export yet", "Grade at least one sheet first.");
      return;
    }
    const quote = value => `"${String(value).replace(/"/g, '""')}"`;
    const stamp = new Date().toISOString().slice(0, 10);
    const rows = [[`Gridlock Sprint team totals ${stamp}`].map(quote).join(",")];
    rows.push(["team", "points", "puzzles solved", "sheets graded"].map(quote).join(","));
    teamTotals().forEach(row => rows.push([row.team, row.points, row.solved, row.sheets].map(quote).join(",")));
    rows.push("");
    rows.push(["team", "puzzle", "points possible", "points awarded", "result",
               "moves used", "optimal moves", "moves", "graded at"].map(quote).join(","));
    Object.values(tally)
      .sort((a, b) => a.team.localeCompare(b.team) || a.code.localeCompare(b.code))
      .forEach(entry => rows.push([
        entry.team, entry.code, entry.possible, entry.awarded, entry.status,
        entry.used, entry.optimal === null ? "" : entry.optimal, entry.moves, entry.at
      ].map(quote).join(",")));

    const blob = new Blob([rows.join("\n")], { type: "text/csv;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `gridlock-scores-${stamp}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  });

  resetTallyButton.addEventListener("click", () => {
    const totals = teamTotals();
    if (!totals.length) return;
    const points = totals.reduce((sum, row) => sum + row.points, 0);
    if (!confirm(`Delete every score? ${points} points across ${totals.length} teams will be lost.`)) return;
    tally = {};
    localStorage.removeItem(STORE_KEY);
    renderTally();
    setResult("neutral", "Scores cleared", "The tally is empty again.");
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

  setMode("sheets");
  loadTally();
})();
