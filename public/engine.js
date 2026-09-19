(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.RushHour = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const SIZE = 6;
  const DIRECTIONS = {
    L: [0, -1], LEFT: [0, -1], "<": [0, -1], "←": [0, -1],
    R: [0, 1], RIGHT: [0, 1], ">": [0, 1], "→": [0, 1],
    U: [-1, 0], UP: [-1, 0], "^": [-1, 0], "↑": [-1, 0],
    D: [1, 0], DOWN: [1, 0], V: [1, 0], "↓": [1, 0]
  };
  const FALLBACKS = {
    easy: [
      ["X",2,0,2,"H"],["A",0,4,3,"V"],["B",2,3,2,"V"],["C",4,0,2,"V"],
      ["D",4,5,2,"V"],["E",4,1,2,"H"],["F",0,0,2,"H"],["G",1,0,2,"H"]
    ],
    medium: [
      ["X",2,0,2,"H"],["A",3,3,3,"V"],["B",4,2,2,"V"],["C",1,4,2,"H"],
      ["D",3,0,2,"V"],["E",0,1,2,"V"],["F",5,4,2,"H"],["G",2,5,3,"V"],
      ["H",3,1,2,"H"],["J",0,3,2,"H"]
    ],
    hard: [
      ["X",2,1,2,"H"],["A",1,5,3,"V"],["B",0,0,2,"H"],["C",4,4,2,"H"],
      ["D",1,1,3,"H"],["E",4,1,2,"V"],["F",3,1,2,"H"],["G",3,3,2,"H"],
      ["H",0,2,3,"H"],["J",1,0,2,"V"],["K",4,2,2,"H"],["L",5,4,2,"H"]
    ]
  };

  function cloneCars(cars) { return cars.map(car => ({ ...car })); }

  function occupied(cars, ignoredId) {
    const grid = Array.from({ length: SIZE }, () => Array(SIZE).fill(null));
    for (const car of cars) {
      if (car.id === ignoredId) continue;
      for (let i = 0; i < car.length; i++) {
        const row = car.row + (car.orientation === "V" ? i : 0);
        const col = car.col + (car.orientation === "H" ? i : 0);
        if (row >= 0 && row < SIZE && col >= 0 && col < SIZE) grid[row][col] = car.id;
      }
    }
    return grid;
  }

  function isSolved(cars) {
    const target = cars.find(car => car.id === "X");
    return Boolean(target && target.orientation === "H" && target.row === 2 && target.col + target.length === SIZE);
  }

  function normalizeDirection(raw) {
    const key = raw.toUpperCase();
    if (["LEFT", "L", "<", "←"].includes(key)) return "L";
    if (["RIGHT", "R", ">", "→"].includes(key)) return "R";
    if (["UP", "U", "^", "↑"].includes(key)) return "U";
    if (["DOWN", "D", "V", "↓"].includes(key)) return "D";
    return null;
  }

  function parseSolution(text) {
    const cleaned = text.trim();
    if (!cleaned) return { moves: [], errors: ["No moves were entered."] };
    const pieces = cleaned
      .replace(/\bTHEN\b/gi, ",")
      .split(/[\n,;]+/)
      .flatMap(piece => piece.trim().split(/\s+(?=[A-Za-z0-9]+\s*(?:left|right|up|down|[LRUDV<>^←→↑↓])\s*\d*)/i))
      .map(piece => piece.replace(/^\s*\d+[.)-]\s*/, "").trim())
      .filter(Boolean);
    const moves = [];
    const errors = [];
    const pattern = /^([A-Za-z0-9]+)\s*(LEFT|RIGHT|UP|DOWN|[LRUDV<>^←→↑↓])\s*(\d+)?$/i;
    pieces.forEach((piece, index) => {
      const match = piece.match(pattern);
      if (!match) {
        errors.push(`Move ${index + 1} (“${piece}”) is not understood.`);
        return;
      }
      const distance = match[3] ? Number(match[3]) : 1;
      if (!Number.isInteger(distance) || distance < 1 || distance > 5) {
        errors.push(`Move ${index + 1} needs a distance from 1 to 5.`);
        return;
      }
      moves.push({ id: match[1].toUpperCase(), direction: normalizeDirection(match[2]), distance, raw: piece });
    });
    return { moves, errors };
  }

  function applyMove(cars, move) {
    const next = cloneCars(cars);
    const car = next.find(item => item.id === move.id);
    if (!car) return { ok: false, reason: `There is no car ${move.id} on this board.` };
    const vector = DIRECTIONS[move.direction];
    if (!vector) return { ok: false, reason: `The direction in ${move.raw || move.id} is invalid.` };
    const verticalMove = vector[0] !== 0;
    if ((car.orientation === "H" && verticalMove) || (car.orientation === "V" && !verticalMove)) {
      return { ok: false, reason: `Car ${car.id} can only move ${car.orientation === "H" ? "left or right" : "up or down"}.` };
    }
    const grid = occupied(next, car.id);
    for (let step = 1; step <= move.distance; step++) {
      const row = car.row + vector[0] * step;
      const col = car.col + vector[1] * step;
      const frontRow = row + (car.orientation === "V" && vector[0] > 0 ? car.length - 1 : 0);
      const frontCol = col + (car.orientation === "H" && vector[1] > 0 ? car.length - 1 : 0);
      if (frontRow < 0 || frontRow >= SIZE || frontCol < 0 || frontCol >= SIZE) {
        return { ok: false, reason: `Car ${car.id} would leave the board.` };
      }
      if (grid[frontRow][frontCol]) {
        return { ok: false, reason: `Car ${car.id} is blocked by car ${grid[frontRow][frontCol]}.` };
      }
    }
    car.row += vector[0] * move.distance;
    car.col += vector[1] * move.distance;
    return { ok: true, cars: next };
  }

  function validateSolution(startCars, text) {
    const parsed = parseSolution(text);
    if (parsed.errors.length) return { status: "error", message: parsed.errors[0], moveNumber: 0 };
    let cars = cloneCars(startCars);
    for (let i = 0; i < parsed.moves.length; i++) {
      const result = applyMove(cars, parsed.moves[i]);
      if (!result.ok) return { status: "error", message: `Move ${i + 1}: ${result.reason}`, moveNumber: i + 1, cars };
      cars = result.cars;
    }
    if (isSolved(cars)) return { status: "success", message: `Solved in ${parsed.moves.length} move${parsed.moves.length === 1 ? "" : "s"}!`, moves: parsed.moves, cars };
    return { status: "partial", message: "Every move is legal, but X has not reached the exit yet.", moves: parsed.moves, cars };
  }

  function stateKey(cars) { return cars.map(car => `${car.row},${car.col}`).join("|"); }

  function legalMoves(cars) {
    const moves = [];
    for (const car of cars) {
      const dirs = car.orientation === "H" ? ["L", "R"] : ["U", "D"];
      for (const direction of dirs) {
        for (let distance = 1; distance < SIZE; distance++) {
          const result = applyMove(cars, { id: car.id, direction, distance });
          if (!result.ok) break;
          moves.push({ move: { id: car.id, direction, distance }, cars: result.cars });
        }
      }
    }
    return moves;
  }

  function solve(startCars, maxStates = 90000) {
    if (isSolved(startCars)) return [];
    const queue = [cloneCars(startCars)];
    const keys = [stateKey(startCars)];
    const seen = new Set(keys);
    const parents = new Map();
    let head = 0;
    while (head < queue.length && seen.size <= maxStates) {
      const cars = queue[head];
      const parentKey = keys[head++];
      for (const option of legalMoves(cars)) {
        const key = stateKey(option.cars);
        if (seen.has(key)) continue;
        seen.add(key);
        parents.set(key, { parentKey, move: option.move });
        if (isSolved(option.cars)) {
          const path = [];
          let cursor = key;
          while (parents.has(cursor)) {
            const item = parents.get(cursor);
            path.push(item.move);
            cursor = item.parentKey;
          }
          return path.reverse();
        }
        queue.push(option.cars);
        keys.push(key);
      }
    }
    return null;
  }

  function createRng(seed) {
    let value = seed >>> 0;
    return function () {
      value += 0x6D2B79F5;
      let t = value;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function randomCandidate(random, carCount) {
    const cars = [{ id: "X", row: 2, col: Math.floor(random() * 3), length: 2, orientation: "H" }];
    const ids = "ABCDEFGHJKLMNPQ";
    for (let index = 0; index < carCount - 1; index++) {
      let placed = false;
      for (let attempt = 0; attempt < 80 && !placed; attempt++) {
        const orientation = random() < .55 ? "V" : "H";
        const length = random() < .28 ? 3 : 2;
        const row = Math.floor(random() * (SIZE - (orientation === "V" ? length : 0) + (orientation === "V" ? 1 : 0)));
        const col = Math.floor(random() * (SIZE - (orientation === "H" ? length : 0) + (orientation === "H" ? 1 : 0)));
        const candidate = { id: ids[index], row, col, length, orientation };
        const grid = occupied(cars);
        let clear = true;
        for (let cell = 0; cell < length; cell++) {
          const r = row + (orientation === "V" ? cell : 0);
          const c = col + (orientation === "H" ? cell : 0);
          if (grid[r][c]) clear = false;
        }
        if (clear) { cars.push(candidate); placed = true; }
      }
      if (!placed) break;
    }
    return cars;
  }

  function generatePuzzle(level = "medium", seed = Date.now()) {
    const ranges = { easy: [3, 5], medium: [6, 9], hard: [10, 18] };
    const [minimum, maximum] = ranges[level] || ranges.medium;
    const random = createRng(seed);
    let fallback = null;
    const attemptLimit = level === "hard" ? 28 : level === "medium" ? 110 : 140;
    for (let attempt = 0; attempt < attemptLimit; attempt++) {
      const count = level === "hard" ? 12 : level === "medium" ? 10 : 8;
      const cars = randomCandidate(random, count);
      if (cars.length !== count || isSolved(cars)) continue;
      const solution = solve(cars, level === "hard" ? 45000 : 55000);
      if (!solution || solution.length < 2) continue;
      if (!fallback || Math.abs(solution.length - minimum) < Math.abs(fallback.solution.length - minimum)) fallback = { cars, solution };
      if (solution.length >= minimum && solution.length <= maximum) return { cars, solution, level };
    }
    if (fallback && fallback.solution.length >= minimum) return { ...fallback, level };
    const cars = FALLBACKS[level].map(([id, row, col, length, orientation]) => ({ id, row, col, length, orientation }));
    return { cars, solution: solve(cars), level };
  }

  function formatMove(move) { return `${move.id}${move.direction}${move.distance}`; }

  return { SIZE, parseSolution, applyMove, validateSolution, isSolved, legalMoves, solve, generatePuzzle, formatMove, cloneCars };
});
