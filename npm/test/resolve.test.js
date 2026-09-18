"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const { resolveRunner, PYPI_PACKAGE } = require("../bin/web-search-mcp.js");

const args = ["search", "giá vàng"];

test("ưu tiên uvx khi có sẵn", () => {
  const runner = resolveRunner(args, { which: (c) => c === "uvx" });
  assert.equal(runner.command, "uvx");
  assert.deepEqual(runner.args, [PYPI_PACKAGE, ...args]);
});

test("rơi về `uv tool run` khi không có uvx", () => {
  const runner = resolveRunner(args, {
    which: (c) => c === "uv",
    detectPython: () => "python",
  });
  assert.equal(runner.command, "uv");
  assert.deepEqual(runner.args, ["tool", "run", PYPI_PACKAGE, ...args]);
});

test("rơi về python -m khi không có uv", () => {
  const runner = resolveRunner(args, {
    which: () => false,
    detectPython: () => "python3",
  });
  assert.equal(runner.command, "python3");
  assert.deepEqual(runner.args, ["-m", "web_search_mcp", ...args]);
});

test("không dò python khi uvx đã có", () => {
  let probedPython = false;
  const runner = resolveRunner(args, {
    which: (c) => c === "uvx",
    detectPython: () => {
      probedPython = true;
      return "python";
    },
  });
  assert.equal(runner.command, "uvx");
  assert.equal(probedPython, false, "không nên dò python khi uvx đã có");
});

test("trả null khi không có gì chạy được", () => {
  const runner = resolveRunner(args, {
    which: () => false,
    detectPython: () => null,
  });
  assert.equal(runner, null);
});