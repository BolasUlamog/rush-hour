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
   * which is why the generator tries many boards for one hard puzzle.
   *
   * Move count is the standard difficulty measure for Rush Hour — ThinkFun's own
   * expert cards run 20 to 50 moves, and the hardest board there is takes 51 —
   * but it does not separate a long forced shuffle from a puzzle with genuine
   * decisions in it. So each tier also asks search.js how much *thought* a start
   * demands (`routeInsight`) and keeps the best board it saw: the one whose
   * shortest solution is a single narrow corridor rather than one of hundreds,
   * and which forces the X car backwards away from the exit on the way.
   */

  const LEVELS = ["easy", "medium", "hard"];
  const LEVEL_CODE = { easy: "E", medium: "M", hard: "H" };
  const LEVEL_NAMES = { easy: "Easy", medium: "Medium", hard: "Hard" };
  // band: shortest-solution length; cars/trucks/vertical: what to place;
  // sift: starts scored per board; pool: boards ranked per puzzle kept.
  const RECIPES = {
    easy: { band: [7, 12], cars: 11, trucks: 0.30, vertical: 0.65, prefer: "spread", sift: 24, pool: 3 },
    medium: { band: [13, 20], cars: 13, trucks: 0.30, vertical: 0.70, prefer: "hardest", sift: 32, pool: 3 },
    hard: { band: [21, 40], cars: 14, trucks: 0.30, vertical: 0.70, prefer: "hardest", sift: 32, pool: 2 }
  };
  const DEFAULTS = {
    counts: { easy: 4, medium: 4, hard: 2 },
    points: { easy: 4, medium: 8, hard: 15 },
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

    const found = search.pickAtDistance(cars, low, high, random, settings.stateLimit,
                                        recipe.prefer, recipe.sift || 0);
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
      stateCount: found.stateCount,
      insight: found.insight || null
    };
  }

  /**
   * Is `puzzle` a more demanding board than `incumbent`, at the same tier?
   *
   * Cone size varies about thirtyfold between boards of identical move count, far
   * more than anything available within a single board, so ranking whole boards is
   * where most of the difficulty is won. A board forcing X backwards beats one
   * that does not; between two of a kind, the narrower corridor wins.
   *
   * `byLength` guards against winning that and losing something bigger. On the
   * tiers that maximize move count, ranking on cone alone will happily trade a
   * 39-move board for a narrower 22-move one — measured, it dropped the hard tier
   * from a mean of 24.1 moves to 22.4. Move count is the standard measure of a
   * Rush Hour puzzle and it is what the tier's points promise, so there it leads
   * and the insight metrics only break its ties. The "spread" tiers vary their
   * length on purpose, so there they rank on insight alone.
   */
  function moreDemanding(puzzle, incumbent, byLength) {
    if (!incumbent) return true;
    if (!puzzle.insight || !incumbent.insight) return false;
    if (byLength && puzzle.shortestMoves !== incumbent.shortestMoves) {
      return puzzle.shortestMoves > incumbent.shortestMoves;
    }
    const forces = puzzle.insight.retreats > 0;
    const held = incumbent.insight.retreats > 0;
    if (forces !== held) return forces;
    return puzzle.insight.cone < incumbent.insight.cone;
  }

  function buildLevel(engine, search, level, count, config, seen) {
    const settings = withDefaults(config);
    const recipe = settings.recipes[level] || RECIPES[level];
    const random = createRandom((settings.seed + level.length * 7919) >>> 0);
    const chosen = [];
    const limit = count * (level === "hard" ? 4000 : 800);
    // Oversample: keep generating past the first acceptable board and hold the
    // most demanding of each group. The search budget is spent either way.
    const pool = Math.max(1, recipe.pool || 1);
    let best = null;
    let inGroup = 0;
    for (let tries = 0; tries < limit && chosen.length < count; tries++) {
      const puzzle = attempt(engine, search, level, settings, seen, random);
      if (!puzzle) continue;
      if (moreDemanding(puzzle, best, recipe.prefer === "hardest")) best = puzzle;
      if (++inGroup >= pool) {
        chosen.push(best);
        best = null;
        inGroup = 0;
      }
    }
    // Ran out of budget mid-group: a board in hand beats none.
    if (best && chosen.length < count) chosen.push(best);
    return chosen;
  }

  function finalize(found, config) {
    const settings = withDefaults(config);
    const order = { easy: 0, medium: 1, hard: 2 };
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
    withDefaults, createRandom, signature, randomLayout, attempt, moreDemanding, buildLevel,
    finalize, buildPuzzleSet
  };
});
