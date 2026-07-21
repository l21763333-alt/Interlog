#!/usr/bin/env node
"use strict";

// Claude Code has one cross-platform hook definition but Python launcher names
// differ by host. This tiny bridge performs argv-safe interpreter discovery and
// forwards the hook JSON byte-for-byte without invoking a shell.
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const input = fs.readFileSync(0);
const script = path.join(__dirname, "iterlog.py");
const reportArgs = process.argv.slice(2);
const configured = (process.env.ITERLOG_PYTHON || "").trim();
const candidates = [];

if (configured) {
  candidates.push({ command: configured, prefix: [] });
}
if (process.platform === "win32") {
  candidates.push(
    { command: "python", prefix: [] },
    { command: "py", prefix: ["-3"] },
    { command: "python3", prefix: [] },
  );
} else {
  candidates.push(
    { command: "python3", prefix: [] },
    { command: "python", prefix: [] },
  );
}

for (const candidate of candidates) {
  const probe = spawnSync(candidate.command, [...candidate.prefix, "--version"], {
    encoding: "utf8",
    env: process.env,
    timeout: 5000,
    windowsHide: true,
  });
  if (probe.error || probe.status !== 0) {
    continue;
  }
  const versionText = `${probe.stdout || ""} ${probe.stderr || ""}`;
  const version = versionText.match(/Python\s+(\d+)\.(\d+)/i);
  if (!version || Number(version[1]) < 3 || (Number(version[1]) === 3 && Number(version[2]) < 10)) {
    continue;
  }
  const result = spawnSync(
    candidate.command,
    [...candidate.prefix, script, ...reportArgs],
    {
      input,
      encoding: null,
      env: {
        ...process.env,
        ITERLOG_HOST: "claude",
        PYTHONIOENCODING: "utf-8",
        PYTHONUTF8: "1",
      },
      maxBuffer: 16 * 1024 * 1024,
      windowsHide: true,
    },
  );
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  if (result.error) {
    process.stderr.write(`Iterlog launcher failed: ${result.error.message}\n`);
    process.exit(1);
  }
  process.exit(result.status === null ? 1 : result.status);
}

let eventName = "";
try {
  eventName = JSON.parse(input.toString("utf8")).hook_event_name || "";
} catch (_error) {
  // The runtime will normally validate input. Here there is no interpreter, so
  // retain only enough information to apply a safe hook failure policy.
}

const message =
  "Iterlog requires Python 3.10+. Set ITERLOG_PYTHON to an absolute interpreter path.";
if (eventName === "PreCompact") {
  process.stdout.write(
    JSON.stringify({ decision: "block", reason: message, systemMessage: message }),
  );
  process.exit(0);
}
process.stdout.write(JSON.stringify({ systemMessage: message }));
process.exit(0);
