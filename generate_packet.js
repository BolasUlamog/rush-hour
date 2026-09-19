#!/usr/bin/env node
"use strict";
/**
 * Prints a packet's puzzle set as JSON. make_packet.py calls this, then renders
 * the PDF, so the puzzle rules stay in engine.js and the set-building rules stay
 * in puzzle_set.js — the same file the in-app generator page uses.
 *
 * Usage: node generate_packet.js '<config json>'
 */

const engine = require("./engine.js");
const search = require("./search.js");
const { buildPuzzleSet } = require("./puzzle_set.js");

function main() {
  let config;
  try {
    config = JSON.parse(process.argv[2] || "{}");
  } catch (error) {
    process.stderr.write(`Could not read the packet configuration: ${error.message}\n`);
    process.exit(2);
  }
  const result = buildPuzzleSet(engine, search, config);
  if (!result.puzzles.length) {
    process.stderr.write("No puzzles could be generated for that configuration.\n");
    process.exit(3);
  }
  result.engine = "engine.js";
  process.stdout.write(JSON.stringify(result, null, 2));
}

main();
