// mcp-probe.js — used by setup/test-install.sh. Starts one MCP server exactly as
// ~/.claude.json registers it, calls one tool over stdio JSON-RPC, prints the reply, and
// exits 0 only when the tool call succeeded.
//
//   node mcp-probe.js <server> <tool> <json arguments>
const fs = require("fs");
const { spawn } = require("child_process");

const [server, tool, args] = process.argv.slice(2);
const cfg = JSON.parse(fs.readFileSync(`${process.env.HOME}/.claude.json`)).mcpServers[server];
const child = spawn(cfg.command, cfg.args, {
  env: { ...process.env, ...cfg.env },
  stdio: ["pipe", "pipe", "ignore"],
});

let buffer = "";
let nextId = 0;
const waiting = {};
child.stdout.on("data", (chunk) => {
  buffer += chunk;
  let end;
  while ((end = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, end);
    buffer = buffer.slice(end + 1);
    if (!line.trim()) continue;
    const message = JSON.parse(line);
    if (message.id && waiting[message.id]) waiting[message.id](message);
  }
});
const send = (message) => child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", ...message })}\n`);
const request = (method, params) =>
  new Promise((resolve) => {
    const id = ++nextId;
    waiting[id] = resolve;
    send({ id, method, params });
  });

setTimeout(() => {
  console.log(`${server}: no reply within 90 seconds`);
  child.kill();
  process.exit(2);
}, 90000);

(async () => {
  await request("initialize", {
    protocolVersion: "2025-06-18",
    capabilities: {},
    clientInfo: { name: "mcp-probe", version: "0" },
  });
  send({ method: "notifications/initialized" });
  const reply = await request("tools/call", { name: tool, arguments: JSON.parse(args) });
  console.log(JSON.stringify(reply));
  child.kill();
  process.exit(reply.result && !reply.result.isError ? 0 : 1);
})();
