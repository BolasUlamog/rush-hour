const assert = require("node:assert/strict");
const engine = require("./public/engine.js");

const board = [
  { id: "X", row: 2, col: 0, length: 2, orientation: "H" },
  { id: "A", row: 1, col: 3, length: 2, orientation: "V" }
];

assert.deepEqual(
  engine.parseSolution("x right 1, aU1\n3. XR3").moves.map(engine.formatMove),
  ["XR1", "AU1", "XR3"]
);
assert.equal(engine.validateSolution(board, "XR2").status, "error");
assert.equal(engine.validateSolution(board, "AU1, XR4").status, "success");
assert.match(engine.validateSolution(board, "AL1").message, /only move up or down/);

for (const level of ["easy", "medium", "hard"]) {
  const puzzle = engine.generatePuzzle(level, 12345 + level.length);
  assert.ok(puzzle);
  assert.ok(puzzle.solution.length > 0);
  assert.equal(
    engine.validateSolution(puzzle.cars, puzzle.solution.map(engine.formatMove).join(",")).status,
    "success"
  );
}

// search.js is a second, much faster implementation of the same rules, used to
// generate puzzles. If it ever disagrees with the referee, puzzles would ship
// with wrong answer keys, so hold the two to the same answers.
const search = require("./public/search.js");
const sets = require("./public/puzzle_set.js");

for (const level of ["easy", "medium", "hard"]) {
  for (let seed = 0; seed < 12; seed++) {
    const puzzle = engine.generatePuzzle(level, 4100 + seed);
    assert.equal(
      search.shortestLength(puzzle.cars),
      puzzle.solution.length,
      `${level} seed ${seed}: fast search disagrees with the engine on the shortest length`
    );
    const route = search.shortestRoute(puzzle.cars).map(engine.formatMove).join(",");
    assert.equal(engine.validateSolution(puzzle.cars, route).status, "success");
  }
}

// A generated set must hit its band, and every puzzle's stated key must solve it.
const built = sets.buildPuzzleSet(engine, search, {
  seed: 20260918,
  counts: { easy: 1, medium: 1, hard: 1 }
});
assert.equal(built.puzzles.length, 3, "every level should yield a puzzle");
for (const puzzle of built.puzzles) {
  const [low, high] = sets.RECIPES[puzzle.level].band;
  assert.ok(
    puzzle.shortestMoves >= low && puzzle.shortestMoves <= high,
    `${puzzle.code} needs ${puzzle.shortestMoves} moves, outside the ${puzzle.level} band ${low}-${high}`
  );
  assert.equal(puzzle.solution.length, puzzle.shortestMoves);
  assert.equal(engine.validateSolution(puzzle.cars, puzzle.solution.join(",")).status, "success");
  // Every car label has to survive the QR round trip as a single character.
  for (const car of puzzle.cars) assert.match(car.id, /^[A-Z0-9]$/);
}
assert.ok(
  built.puzzles.find(p => p.level === "hard").shortestMoves >= 21,
  "a hard puzzle should need at least 21 moves"
);

// Difficulty is not only length. Selection is supposed to prefer a start whose
// shortest solution is a narrow corridor, and one that makes the X car reverse
// away from the exit — the move solvers do not think to look for. Measured on the
// same boards, sifting must not quietly hand back a wider cone than doing nothing.
{
  const recipe = sets.RECIPES.medium;
  const random = sets.createRandom(4242);
  let compared = 0;
  let siftedNarrower = 0;
  let forcesRetreat = 0;
  for (let tries = 0; tries < 400 && compared < 20; tries++) {
    const cars = sets.randomLayout(random, recipe);
    const [low, high] = recipe.band;
    const plain = search.pickAtDistance(cars, low, high, sets.createRandom(tries), 400000, "hardest", 0);
    const sifted = search.pickAtDistance(cars, low, high, sets.createRandom(tries), 400000, "hardest", recipe.sift);
    if (!plain || !plain.cars || !sifted || !sifted.cars) continue;
    assert.equal(plain.moves, sifted.moves, "sifting must not change the move count");
    compared += 1;
    if (sifted.insight.cone <= plain.insight.cone) siftedNarrower += 1;
    if (sifted.insight.retreats > 0) forcesRetreat += 1;
  }
  assert.ok(compared >= 10, `only ${compared} boards were comparable`);
  assert.equal(siftedNarrower, compared,
    `sifting widened the solution cone on ${compared - siftedNarrower} of ${compared} boards`);
  assert.ok(forcesRetreat / compared >= 0.5,
    `only ${forcesRetreat} of ${compared} sifted starts force the X car backwards`);
}

console.log("All Gridlock Sprint engine tests passed.");
