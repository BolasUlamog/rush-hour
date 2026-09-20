(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.RushSearch = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  /**
   * Fast state-space search over a Gridlock board.
   *
   * engine.js stays the referee — it parses and judges a student's route. This
   * module exists because generating a genuinely hard puzzle means walking the
   * whole state graph, and engine.js clones the car list for every candidate
   * move, which is far too slow for that. Here the board is a flat Uint8Array
   * mutated in place, which measures about 170x faster.
   *
   * The important idea in `hardestStart`: a random board is usually easy. The
   * hard puzzle is the *position* within that board's state graph that sits
   * farthest from any solved position, so we measure every reachable position's
   * distance to the exit and take the worst one. That is what lifts generation
   * from roughly a dozen moves to forty-plus.
   */

  const SIZE = 6;
  const CELLS = SIZE * SIZE;
  const DEFAULT_LIMIT = 400000;

  function makeBoard(cars) {
    const count = cars.length;
    const board = {
      count,
      vertical: new Uint8Array(count),
      length: new Uint8Array(count),
      line: new Uint8Array(count),
      ids: cars.map(car => String(car.id).toUpperCase()),
      grid: new Uint8Array(CELLS)
    };
    for (let i = 0; i < count; i++) {
      const car = cars[i];
      board.vertical[i] = car.orientation === "V" ? 1 : 0;
      board.length[i] = car.length;
      board.line[i] = board.vertical[i] ? car.col : car.row;
    }
    board.target = board.ids.indexOf("X");
    return board;
  }

  function positionsOf(board, cars) {
    const pos = new Uint8Array(board.count);
    for (let i = 0; i < board.count; i++) pos[i] = board.vertical[i] ? cars[i].row : cars[i].col;
    return pos;
  }

  function cellAt(board, car, position, offset) {
    return board.vertical[car]
      ? (position + offset) * SIZE + board.line[car]
      : board.line[car] * SIZE + (position + offset);
  }

  function paint(board, pos) {
    board.grid.fill(0);
    for (let i = 0; i < board.count; i++) {
      for (let k = 0; k < board.length[i]; k++) board.grid[cellAt(board, i, pos[i], k)] = i + 1;
    }
  }

  function keyOf(pos) {
    let key = "";
    for (let i = 0; i < pos.length; i++) key += String.fromCharCode(pos[i]);
    return key;
  }

  function isSolvedPosition(board, pos) {
    return pos[board.target] === SIZE - board.length[board.target];
  }

  /** Call `visit(carIndex, newPosition)` for every legal slide from `pos`. */
  function eachMove(board, pos, visit) {
    paint(board, pos);
    for (let car = 0; car < board.count; car++) {
      const limit = SIZE - board.length[car];
      for (let step = -1; step <= 1; step += 2) {
        for (let next = pos[car] + step; next >= 0 && next <= limit; next += step) {
          const leading = step > 0 ? next + board.length[car] - 1 : next;
          if (board.grid[cellAt(board, car, leading, 0)]) break;
          visit(car, next);
        }
      }
    }
  }

  /** Every position reachable from `startPos`, or null past `limit` states. */
  function explore(board, startPos, limit) {
    const positions = [Uint8Array.from(startPos)];
    const index = new Map([[keyOf(startPos), 0]]);
    for (let head = 0; head < positions.length; head++) {
      if (positions.length > limit) return null;
      const pos = positions[head];
      eachMove(board, pos, (car, next) => {
        const candidate = Uint8Array.from(pos);
        candidate[car] = next;
        const key = keyOf(candidate);
        if (index.has(key)) return;
        index.set(key, positions.length);
        positions.push(candidate);
      });
    }
    return { positions, index };
  }

  /** Distance from every reachable position to the nearest solved one. */
  function distancesToExit(board, component) {
    const { positions, index } = component;
    const distance = new Int32Array(positions.length).fill(-1);
    const queue = [];
    for (let i = 0; i < positions.length; i++) {
      if (isSolvedPosition(board, positions[i])) {
        distance[i] = 0;
        queue.push(i);
      }
    }
    // Every slide can be undone, so the graph is undirected and one sweep out
    // from the solved positions gives the true distance for all of them.
    for (let head = 0; head < queue.length; head++) {
      const at = queue[head];
      const pos = positions[at];
      eachMove(board, pos, (car, next) => {
        const candidate = Uint8Array.from(pos);
        candidate[car] = next;
        const to = index.get(keyOf(candidate));
        if (to === undefined || distance[to] !== -1) return;
        distance[to] = distance[at] + 1;
        queue.push(to);
      });
    }
    return distance;
  }

  function toCars(board, pos, template) {
    return template.map((car, i) => ({
      id: car.id,
      length: car.length,
      orientation: car.orientation,
      row: board.vertical[i] ? pos[i] : board.line[i],
      col: board.vertical[i] ? board.line[i] : pos[i]
    }));
  }

  /**
   * How much thinking a start position demands, on top of its move count.
   *
   * Move count alone does not tell a long forced shuffle apart from a puzzle with
   * real decisions in it. Both take thirty moves; only one of them is interesting.
   * So this walks every state lying on *some* shortest solution and asks, at each
   * one, how many of the legal moves actually make progress:
   *
   *   cone      how many states lie on *some* shortest solution. This is the one
   *             that matters. A wide cone means many different shortest routes,
   *             so blundering forward tends to work; a narrow one means a single
   *             corridor that has to be found. At a fixed 16 moves this ranges
   *             over 21 to 688 across boards, so it separates them sharply.
   *   retreats  states where a shortest continuation drives X *away* from the
   *             exit. This is the move solvers refuse to look for, and what makes
   *             a puzzle feel like it needs insight rather than patience.
   *   forks     states offering five or more moves of which exactly one is right.
   *   trapRate  share of legal moves that do not shorten the solution.
   *
   * Only `cone` and `retreats` drive selection. `forks` and `trapRate` are kept
   * because they are what a solver actually feels, but neither can choose between
   * boards: trapRate sits near 0.80 on everything, and raw fork counts correlate
   * 0.87 with cone size, so maximizing forks would quietly select *wide* cones —
   * easier puzzles — while looking like it was picking harder ones.
   *
   * There is no accepted difficulty formula for Rush Hour beyond move count, so
   * these stay separate rather than blended into one number with invented weights.
   */
  function routeInsight(board, component, distance, startIndex, coneLimit = 20000) {
    const { positions, index } = component;
    if (distance[startIndex] < 1) return { trapRate: 0, forks: 0, retreats: 0, cone: 0 };

    const seen = new Set([startIndex]);
    const stack = [startIndex];
    let legalSeen = 0;
    let goodSeen = 0;
    let forks = 0;
    let retreats = 0;

    while (stack.length) {
      const at = stack.pop();
      const here = distance[at];
      if (here === 0) continue;
      const pos = positions[at];
      const onward = [];
      let legal = 0;
      let backward = 0;
      eachMove(board, pos, (car, next) => {
        legal += 1;
        const candidate = Uint8Array.from(pos);
        candidate[car] = next;
        const to = index.get(keyOf(candidate));
        if (to === undefined || distance[to] !== here - 1) return;
        if (car === board.target && next < pos[car]) backward += 1;
        onward.push(to);
      });
      legalSeen += legal;
      goodSeen += onward.length;
      if (onward.length === 1 && legal >= 5) forks += 1;
      if (backward > 0) retreats += 1;
      for (const to of onward) {
        if (seen.has(to) || seen.size >= coneLimit) continue;
        seen.add(to);
        stack.push(to);
      }
    }
    return {
      trapRate: legalSeen ? (legalSeen - goodSeen) / legalSeen : 0,
      forks,
      retreats,
      cone: seen.size
    };
  }

  /**
   * Pick a starting position whose distance to the exit falls in [low, high].
   *
   * One exploration of a car set yields a position for every achievable move
   * count, so the same work serves any difficulty band. Pass prefer="hardest" to
   * take the longest start available inside the band. Also reports the hardest
   * distance this car set can offer at all.
   */
  function pickAtDistance(cars, low, high, random = Math.random, limit = DEFAULT_LIMIT,
                          prefer = "spread", sift = 0) {
    const board = makeBoard(cars);
    if (board.target < 0 || board.vertical[board.target]) return null;
    const component = explore(board, positionsOf(board, cars), limit);
    if (!component) return null;
    const distance = distancesToExit(board, component);

    let hardest = -1;
    const byDistance = new Map();
    for (let i = 0; i < distance.length; i++) {
      if (distance[i] > hardest) hardest = distance[i];
      if (distance[i] < low || distance[i] > high) continue;
      const bucket = byDistance.get(distance[i]);
      if (bucket) bucket.push(i);
      else byDistance.set(distance[i], [i]);
    }
    if (hardest < 1) return null;
    if (!byDistance.size) return { cars: null, moves: 0, hardest, stateCount: component.positions.length };
    // Sample a move count first, then a position with it. Sampling positions
    // directly would land on the easy end of the band almost every time, since
    // short-distance positions vastly outnumber long-distance ones.
    const lengths = [...byDistance.keys()].sort((a, b) => a - b);
    // "hardest" takes the longest start this board can offer inside the band,
    // which is what the upper difficulties want; "spread" varies the length.
    const wanted = prefer === "hardest"
      ? lengths[lengths.length - 1]
      : lengths[Math.floor(random() * lengths.length)];
    const bucket = byDistance.get(wanted);

    // Every start in this bucket takes the same number of moves, so move count
    // cannot separate them — but how much thought they demand varies enormously.
    // Score a sample and keep the narrowest corridor, preferring one that forces
    // X backward at some point.
    let chosen = bucket[Math.floor(random() * bucket.length)];
    let insight = null;
    if (sift > 0 && bucket.length > 1) {
      const pool = bucket.slice();
      for (let i = pool.length - 1; i > 0; i--) {
        const j = Math.floor(random() * (i + 1));
        [pool[i], pool[j]] = [pool[j], pool[i]];
      }
      // A start that forces X backward always beats one that does not; between
      // two of the same kind, the narrower corridor wins.
      function beats(scored, incumbent) {
        if (!incumbent) return true;
        const forces = scored.retreats > 0;
        const held = incumbent.scored.retreats > 0;
        if (forces !== held) return forces;
        return scored.cone < incumbent.scored.cone;
      }

      let best = null;
      for (const candidate of pool.slice(0, sift)) {
        const scored = routeInsight(board, component, distance, candidate);
        if (beats(scored, best)) best = { candidate, scored };
      }
      if (best) {
        chosen = best.candidate;
        insight = best.scored;
      }
    }
    if (!insight) insight = routeInsight(board, component, distance, chosen);
    return {
      cars: toCars(board, component.positions[chosen], cars),
      moves: distance[chosen],
      hardest,
      stateCount: component.positions.length,
      insight
    };
  }

  /**
   * The hardest solvable starting position for this set of cars.
   * Returns null if the cars can never reach the exit, or the graph is too big.
   */
  function hardestStart(cars, limit = DEFAULT_LIMIT) {
    const board = makeBoard(cars);
    if (board.target < 0 || board.vertical[board.target]) return null;
    const component = explore(board, positionsOf(board, cars), limit);
    if (!component) return null;
    const distance = distancesToExit(board, component);
    let best = -1;
    let bestIndex = -1;
    for (let i = 0; i < distance.length; i++) {
      if (distance[i] > best) {
        best = distance[i];
        bestIndex = i;
      }
    }
    if (bestIndex < 0 || best < 1) return null;
    return {
      cars: toCars(board, component.positions[bestIndex], cars),
      moves: best,
      stateCount: component.positions.length
    };
  }

  /** A shortest route as engine-style moves, or null if unsolvable. */
  function shortestRoute(cars, limit = DEFAULT_LIMIT) {
    const board = makeBoard(cars);
    if (board.target < 0) return null;
    const start = positionsOf(board, cars);
    if (isSolvedPosition(board, start)) return [];
    const positions = [start];
    const index = new Map([[keyOf(start), 0]]);
    const parents = [-1];
    const steps = [null];
    for (let head = 0; head < positions.length; head++) {
      if (positions.length > limit) return null;
      const pos = positions[head];
      let solved = -1;
      eachMove(board, pos, (car, next) => {
        if (solved >= 0) return;
        const candidate = Uint8Array.from(pos);
        candidate[car] = next;
        const key = keyOf(candidate);
        if (index.has(key)) return;
        index.set(key, positions.length);
        parents.push(head);
        steps.push({ car, from: pos[car], to: next });
        positions.push(candidate);
        if (isSolvedPosition(board, candidate)) solved = positions.length - 1;
      });
      if (solved >= 0) {
        const route = [];
        for (let at = solved; at > 0; at = parents[at]) {
          const step = steps[at];
          const distance = Math.abs(step.to - step.from);
          const direction = board.vertical[step.car]
            ? (step.to > step.from ? "D" : "U")
            : (step.to > step.from ? "R" : "L");
          route.push({ id: board.ids[step.car], direction, distance });
        }
        return route.reverse();
      }
    }
    return null;
  }

  function shortestLength(cars, limit = DEFAULT_LIMIT) {
    const route = shortestRoute(cars, limit);
    return route ? route.length : null;
  }

  return { SIZE, makeBoard, positionsOf, explore, distancesToExit, routeInsight, pickAtDistance,
           hardestStart, shortestRoute, shortestLength };
});
