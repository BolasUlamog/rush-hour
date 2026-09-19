(function () {
  "use strict";
  /**
   * Puzzle generator page.
   *
   * Puzzles are built here in the browser by engine.js, so the boards obey
   * exactly the rules the grader will apply. The server is asked only to lay the
   * chosen boards out as PDF pages; it keeps nothing, because every sheet
   * carries its own puzzle in a QR square.
   */
  const engine = window.RushHour;
  const search = window.RushSearch;
  const sets = window.PuzzleSet;

  const controls = {
    easy: document.getElementById("countEasy"),
    medium: document.getElementById("countMedium"),
    hard: document.getElementById("countHard"),
    grandmaster: document.getElementById("countGrandmaster")
  };
  const points = {
    easy: document.getElementById("pointsEasy"),
    medium: document.getElementById("pointsMedium"),
    hard: document.getElementById("pointsHard"),
    grandmaster: document.getElementById("pointsGrandmaster")
  };
  const packetLabel = document.getElementById("packetLabel");
  const packetSeed = document.getElementById("packetSeed");
  const generateButton = document.getElementById("generateButton");
  const resultBox = document.getElementById("makeResult");
  const output = document.getElementById("makeOutput");
  const table = document.getElementById("makeTable");
  const badge = document.getElementById("makeBadge");
  const downloadSheets = document.getElementById("downloadSheets");
  const downloadKey = document.getElementById("downloadKey");

  let built = null;

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

  function readNumber(input, fallback) {
    const value = Number.parseInt(input.value, 10);
    return Number.isFinite(value) && value >= 0 ? value : fallback;
  }

  function config() {
    const seed = Number.parseInt(packetSeed.value, 10);
    return {
      counts: {
        easy: readNumber(controls.easy, 0),
        medium: readNumber(controls.medium, 0),
        hard: readNumber(controls.hard, 0),
        grandmaster: readNumber(controls.grandmaster, 0)
      },
      points: {
        easy: readNumber(points.easy, 2),
        medium: readNumber(points.medium, 4),
        hard: readNumber(points.hard, 8),
        grandmaster: readNumber(points.grandmaster, 15)
      },
      packetId: (packetLabel.value || "").trim().toUpperCase(),
      seed: Number.isFinite(seed) ? seed : undefined
    };
  }

  function renderTable(result) {
    table.replaceChildren();
    const element = document.createElement("table");
    const head = document.createElement("thead");
    head.innerHTML = "<tr><th>Page</th><th>Puzzle</th><th>Level</th><th>Optimal</th><th>Points</th></tr>";
    const body = document.createElement("tbody");
    result.puzzles.forEach(puzzle => {
      const row = document.createElement("tr");
      [puzzle.index, puzzle.code, sets.LEVEL_NAMES[puzzle.level] || puzzle.level,
       `${puzzle.shortestMoves} moves`, puzzle.points]
        .forEach((value, index) => {
          const cell = document.createElement(index === 1 ? "th" : "td");
          cell.textContent = value;
          row.append(cell);
        });
      body.append(row);
    });
    element.append(head, body);
    table.append(element);
  }

  async function build() {
    const settings = sets.withDefaults(config());
    const wanted = sets.LEVELS.reduce((sum, level) => sum + Number(settings.counts[level] || 0), 0);
    if (wanted <= 0) {
      setResult("error", "Ask for at least one puzzle", "Set a count above zero for at least one level.");
      return;
    }

    generateButton.disabled = true;
    generateButton.firstChild.textContent = "Searching for solvable boards… ";
    output.hidden = true;
    setResult("partial", "Generating", `Looking for ${wanted} distinct solvable boards.`);

    const seen = new Set();
    const found = [];
    const shortfalls = [];
    try {
      for (const level of sets.LEVELS) {
        const count = Number(settings.counts[level] || 0);
        if (count <= 0) continue;
        const random = sets.createRandom((settings.seed + level.length * 7919) >>> 0);
        const limit = count * (level === "grandmaster" ? 4000 : 800);
        let made = 0;
        for (let tries = 0; tries < limit && made < count; tries++) {
          const puzzle = sets.attempt(engine, search, level, settings, seen, random);
          if (puzzle) {
            found.push(puzzle);
            made += 1;
          }
          // Hand the page back to the browser regularly: a grandmaster board can
          // take a couple of seconds of searching to turn up.
          if (tries % 25 === 0) {
            setResult("partial", "Generating",
              `${found.length} of ${wanted} found — searching ${sets.LEVEL_NAMES[level].toLowerCase()} boards.`);
            await new Promise(resolve => setTimeout(resolve, 0));
          }
        }
        if (made < count) shortfalls.push(`${level}: found ${made} of ${count}`);
      }
      if (!found.length) {
        setResult("error", "No puzzles could be generated", "Try fewer hard puzzles, or a different seed.");
        return;
      }
      built = sets.finalize(found, settings);
      renderTable(built);
      output.hidden = false;
      badge.textContent = `${built.puzzles.length} pages · ${built.totalPoints} pts`;
      if (shortfalls.length) {
        setResult("partial", `Generated ${built.puzzles.length} puzzles`,
          `${shortfalls.join("; ")}. Download the sheets, or generate again for a different set.`);
      } else {
        setResult("success", `Generated ${built.puzzles.length} puzzles`,
          `${built.totalPoints} points in total. Print the student sheets at 100% scale.`);
      }
    } catch (error) {
      setResult("error", "Generation failed", error.message);
    } finally {
      generateButton.disabled = false;
      generateButton.firstChild.textContent = "Generate puzzles ";
    }
  }

  async function download(kind, button) {
    if (!built) return;
    const label = button.firstChild.textContent;
    button.disabled = true;
    try {
      const response = await fetch("/api/sheets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          kind,
          packetId: built.packetId,
          title: built.title,
          round: built.round,
          puzzles: built.puzzles
        })
      });
      if (!response.ok) {
        let message = "The sheets could not be laid out.";
        try {
          message = (await response.json()).error || message;
        } catch (error) { /* the server sent a PDF or nothing useful */ }
        throw new Error(message);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `gridlock-${built.packetId}-${kind === "key" ? "key" : "sheets"}.pdf`;
      link.click();
      // Also open it, so printing is one step rather than a trip to Downloads.
      window.open(url, "_blank");
      setTimeout(() => URL.revokeObjectURL(url), 30000);
    } catch (error) {
      setResult("error", "Could not build the PDF", location.protocol === "file:"
        ? "Start the app through Start Grading Station rather than opening index.html directly."
        : error.message);
    } finally {
      button.disabled = false;
      button.firstChild.textContent = label;
    }
  }

  generateButton.addEventListener("click", build);
  downloadSheets.addEventListener("click", () => download("packet", downloadSheets));
  downloadKey.addEventListener("click", () => download("key", downloadKey));
})();
