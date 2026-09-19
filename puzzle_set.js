(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.PuzzleSet = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  /**
   * Chooses a set of distinct puzzles for one packet.
   *
   * Shared by the in-app generator page and generate_packet.js, so the
   * difficulty bands and the points exist in one place only.
   *
   * Difficulty does not come from sprinkling in more cars — measured over
   * thousands of boards, packing in more traffic makes puzzles *easier*, because
   * a crowded board has fewer legal moves. It comes from search.js: for a given
   * set of cars, walk the whole state graph and start the puzzle at a position
   * far from the exit. Roughly 2% of boards can support a 25-move-plus start,
   * which is why the generator tries many boards for one grandmaster puzzle.
   */

  const LEVELS = ["easy", "medium", "hard", "grandmaster"];
  const LEVEL_CODE = { easy: "E", medium: "M", hard: "H", grandmaster: "G" };
  const LEVEL_NAMES = {
    easy: "Easy", medium: "Medium", hard: "Hard", grandmaster: "Grandmaster"
  };
  // band: shortest-solution length; cars/trucks/vertical: what to place.
  const RECIPES = {
    easy: { band: [3, 6], cars: 9, trucks: 0.25, vertical: 0.60, prefer: "spread" },
    medium: { band: [7, 12], cars: 11, trucks: 0.30, vertical: 0.65, prefer: "spread" },
    hard: { band: [13, 20], cars: 13, trucks: 0.30, vertical: 0.70, prefer: "hardest" },
    grandmaster: { band: [21, 40], cars: 14, trucks: 0.30, vertical: 0.70, prefer: "hardest" }
  };
  const DEFAULTS = {
    counts: { easy: 4, medium: 4, hard: 2, grandmaster: 1 },
    points: { easy: 2, medium: 4, hard: 8, grandmaster: 15 },
    title: "Middle School Math Meet",
    round: "Gridlock Sprint",
    stateLimit: 400000
  };
  const SIZE = 6;
  const CAR_IDS = "ABCDEFGHJKLMNPQRSTUV";   // I and O look like 1 and 0 by hand
  const TARGET_ROW = 2;

  function withDefaults(config) {
    const given = config || {};
    return {
      ...DEFAULTS,
      ...given,
      counts: { ...DEFAULTS.counts, ...(given.counts || {}) },
      points: { ...DEFAULTS.points, ...(given.points || {}) },
      recipes: { ...RECIPES, ...(given.recipes || {}) },
      seed: Number.isFinite(given.seed) ? given.seed : Date.now() % 2147483647
    };
  }

  function createRandom(seed) {
    let value = seed >>> 0;
    return function () {
      value += 0x6D2B79F5;
      let t = value;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function signature(cars) {
    return cars
      .map(car => `${car.id}${car.row}${car.col}${car.length}${car.orientation}`)
      .sort()
      .join("|");
  }

  /**
   * A random set of cars around the X car.
   *
   * Horizontal traffic is kept out of X's row: such a car could never be passed
   * or driven aside, so any board with one to the right of X is unsolvable.
   */
  function randomLayout(random, recipe) {
    const cars = [{ id: "X", row: TARGET_ROW, col: 0, length: 2, orientation: "H" }];
    const grid = new Uint8Array(SIZE * SIZE);
    grid[TARGET_ROW * SIZE] = 1;
    grid[TARGET_ROW * SIZE + 1] = 1;

    for (let placed = 0; placed < recipe.cars - 1; placed++) {
      for (let attempt = 0; attempt < 150; attempt++) {
        const vertical = random() < recipe.vertical;
        const length = random() < recipe.trucks ? 3 : 2;
        const line = Math.floor(random() * SIZE);
        const start = Math.floor(random() * (SIZE - length + 1));
        if (!vertical && line === TARGET_ROW) continue;
        let clear = true;
        for (let k = 0; k < length && clear; k++) {
          const cell = vertical ? (start + k) * SIZE + line : line * SIZE + (start + k);
          if (grid[cell]) clear = false;
        }
        if (!clear) continue;
        for (let k = 0; k < length; k++) {
          const cell = vertical ? (start + k) * SIZE + line : line * SIZE + (start + k);
          grid[cell] = placed + 2;
        }
        cars.push({
          id: CAR_IDS[placed],
          row: vertical ? start : line,
          col: vertical ? line : start,
          length,
          orientation: vertical ? "V" : "H"
        });
        break;
      }
    }
    return cars;
  }

  /** One attempt at one puzzle: lay out cars, then find a hard start for them. */
  function attempt(engine, search, level, settings, seen, random) {
    const recipe = settings.recipes[level] || RECIPES[level];
    const [low, high] = recipe.band;
    const cars = randomLayout(random, recipe);
    if (cars.length < Math.min(8, recipe.cars)) return null;

    const found = search.pickAtDistance(cars, low, high, random, settings.stateLimit, recipe.prefer);
    if (!found || !found.cars) return null;

    const key = signature(found.cars);
    if (seen.has(key)) return null;

    // The referee has to agree the route solves it before the puzzle is used.
    const route = search.shortestRoute(found.cars, settings.stateLimit);
    if (!route || route.length !== found.moves) return null;
    const written = route.map(engine.formatMove);
    if (engine.validateSolution(found.cars, written.join(",")).status !== "success") return null;

    seen.add(key);
    return {
      level,
      cars: found.cars,
      solution: written,
      shortestMoves: found.moves,
      hardestPossible: found.hardest,
      stateCount: found.stateCount
    };
  }

  function buildLevel(engine, search, level, count, config, seen) {
    const settings = withDefaults(config);
    const random = createRandom((settings.seed + level.length * 7919) >>> 0);
    const chosen = [];
    const limit = count * (level === "grandmaster" ? 4000 : 800);
    for (let tries = 0; tries < limit && chosen.length < count; tries++) {
      const puzzle = attempt(engine, search, level, settings, seen, random);
      if (puzzle) chosen.push(puzzle);
    }
    return chosen;
  }

  function finalize(found, config) {
    const settings = withDefaults(config);
    const order = { easy: 0, medium: 1, hard: 2, grandmaster: 3 };
    const sorted = [...found].sort((a, b) => order[a.level] - order[b.level]);
    const puzzles = sorted.map((puzzle, index) => ({
      index: index + 1,
      code: `GS-${LEVEL_CODE[puzzle.level]}-${String(index + 1).padStart(3, "0")}`,
      level: puzzle.level,
      points: Number(settings.points[puzzle.level]),
      shortestMoves: puzzle.shortestMoves,
      cars: puzzle.cars,
      solution: puzzle.solution
    }));
    return {
      packetId: settings.packetId || `GS-${settings.seed}`,
      title: settings.title,
      round: settings.round,
      seed: settings.seed,
      puzzles,
      totalPoints: puzzles.reduce((sum, puzzle) => sum + puzzle.points, 0),
      warnings: []
    };
  }

  function buildPuzzleSet(engine, search, config) {
    const settings = withDefaults(config);
    const seen = new Set();
    const found = [];
    const warnings = [];
    for (const level of LEVELS) {
      const wanted = Number(settings.counts[level] || 0);
      if (wanted <= 0) continue;
      const chosen = buildLevel(engine, search, level, wanted, settings, seen);
      if (chosen.length < wanted) warnings.push(`${level}: asked for ${wanted}, found ${chosen.length}`);
      found.push(...chosen);
    }
    const result = finalize(found, settings);
    result.warnings = warnings;
    return result;
  }

  return {
    LEVELS, LEVEL_CODE, LEVEL_NAMES, RECIPES, DEFAULTS,
    withDefaults, createRandom, signature, randomLayout, attempt, buildLevel, finalize, buildPuzzleSet
  };
});
