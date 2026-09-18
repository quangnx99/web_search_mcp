#!/usr/bin/env node
"use strict";

/**
 * Shim npm cho web-search-mcp.
 *
 * Server thật viết bằng Python và phát hành trên PyPI. Gói npm này không chứa
 * code Python; nó chỉ chạy server qua `uvx` (hoặc `python -m web_search_mcp`
 * nếu đã cài bằng pip) và chuyển tiếp stdio nguyên vẹn.
 *
 * stdio phải inherit: MCP giao tiếp qua stdin/stdout, thêm bất kỳ lớp đệm nào
 * cũng làm hỏng giao thức.
 */

const { spawn, spawnSync } = require("node:child_process");

/** Tên distribution trên PyPI (khác tên lệnh và khác tên package npm). */
const PYPI_PACKAGE = "web-search-mcp-free";

/** Tên executable do package cung cấp — không đổi dù distribution đổi tên. */
const CONSOLE_COMMAND = "web-search-mcp";

const INSTALL_HINT = `Không tìm thấy cách chạy server web-search-mcp.

Cần một trong hai:
  1) uv  — https://docs.astral.sh/uv/getting-started/installation/
         Windows:      powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
         macOS/Linux:  curl -LsSf https://astral.sh/uv/install.sh | sh
  2) Python >= 3.12 với package đã cài:
         pip install web-search-mcp-free

Sau đó thử lại. Hoặc chạy trực tiếp không qua npm:
  uvx ${PYPI_PACKAGE}
`;

/** Có lệnh này trên PATH không? Dùng spawnSync nên không cần shell. */
function defaultWhich(command) {
  const probe = spawnSync(command, ["--version"], {
    stdio: "ignore",
    shell: false,
  });
  return !probe.error;
}

/** Tìm interpreter Python đã có sẵn package. Trả tên lệnh hoặc null. */
function defaultDetectPython() {
  const names = process.platform === "win32" ? ["python", "py"] : ["python3", "python"];
  for (const name of names) {
    const probe = spawnSync(name, ["-m", "web_search_mcp", "--version"], {
      stdio: "ignore",
      shell: false,
    });
    if (!probe.error && probe.status === 0) return name;
  }
  return null;
}

/**
 * Chọn cách chạy server. Ưu tiên `uvx` vì không cần cài Python package trước.
 * Trả { label, command, args } hoặc null nếu không có gì dùng được.
 */
function resolveRunner(args, deps = {}) {
  const which = deps.which || defaultWhich;
  const detectPython = deps.detectPython || defaultDetectPython;

  // Dùng `--from` thay vì `uvx ${PYPI_PACKAGE}`: uvx chỉ chạy bare name khi
  // package có executable TRÙNG tên gói, mà điều đó chỉ đúng từ 0.1.1. Dạng
  // `--from` chạy được với mọi bản đã phát hành.
  const viaUv = [
    ["uvx", ["--from", PYPI_PACKAGE, CONSOLE_COMMAND]],
    ["uv", ["tool", "run", "--from", PYPI_PACKAGE, CONSOLE_COMMAND]],
  ];
  for (const [command, prefix] of viaUv) {
    if (which(command)) {
      return { label: command, command, args: [...prefix, ...args] };
    }
  }

  const python = detectPython();
  if (python) {
    return {
      label: `${python} -m web_search_mcp`,
      command: python,
      args: ["-m", "web_search_mcp", ...args],
    };
  }

  return null;
}

function run(runner) {
  const child = spawn(runner.command, runner.args, {
    stdio: "inherit",
    env: process.env,
    shell: false,
  });

  child.on("error", (err) => {
    process.stderr.write(
      `web-search-mcp: không chạy được ${runner.label}: ${err.message}\n`
    );
    process.exit(1);
  });

  // Chuyển tiếp tín hiệu để Ctrl+C dừng cả tiến trình con, tránh treo.
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.on(signal, () => child.kill(signal));
  }

  child.on("exit", (code, signal) => {
    if (signal) {
      process.stderr.write(`web-search-mcp: tiến trình con dừng bởi ${signal}\n`);
      process.exit(1);
    }
    process.exit(code === null ? 1 : code);
  });
}

function main() {
  const args = process.argv.slice(2);
  const runner = resolveRunner(args);

  if (!runner) {
    process.stderr.write(INSTALL_HINT);
    process.exit(1);
  }

  run(runner);
}

if (require.main === module) {
  main();
}

module.exports = { resolveRunner, PYPI_PACKAGE, CONSOLE_COMMAND, INSTALL_HINT };