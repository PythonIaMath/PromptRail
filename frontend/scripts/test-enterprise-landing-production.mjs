import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import net from "node:net";

const host = "127.0.0.1";
const startupTimeoutMs = 30_000;

function reservePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();

    server.once("error", reject);
    server.listen(0, host, () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : null;

      server.close((error) => {
        if (error) {
          reject(error);
          return;
        }

        if (!port) {
          reject(new Error("Unable to reserve a port for the production smoke test."));
          return;
        }

        resolve(port);
      });
    });
  });
}

async function waitForRoot(url, child, output) {
  const deadline = Date.now() + startupTimeoutMs;

  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`Next.js exited before becoming ready.\n${output.join("")}`);
    }

    try {
      const response = await fetch(url);
      if (response.ok) {
        return response;
      }
    } catch {
      // The server is still starting.
    }

    await new Promise((resolve) => setTimeout(resolve, 250));
  }

  throw new Error(`Timed out waiting for ${url}.\n${output.join("")}`);
}

const port = await reservePort();
const url = `http://${host}:${port}/`;
const output = [];
const child = spawn(
  process.execPath,
  ["node_modules/next/dist/bin/next", "start", "--hostname", host, "--port", String(port)],
  {
    cwd: new URL("..", import.meta.url),
    env: {
      ...process.env,
      MONGODB_URI: process.env.MONGODB_URI || "mongodb://127.0.0.1:27017/promptrail_test",
    },
    stdio: ["ignore", "pipe", "pipe"],
  },
);

child.stdout.on("data", (chunk) => output.push(chunk.toString()));
child.stderr.on("data", (chunk) => output.push(chunk.toString()));

try {
  const response = await waitForRoot(url, child, output);
  const html = await response.text();

  assert.match(html, /landing-page landing-page-enterprise/);
  assert.match(html, /Save 70% on token costs/);
  assert.match(html, /without your users noticing\./);
  assert.match(html, /Join The Waitlist/);
  assert.doesNotMatch(html, /Stop overpaying/);
} finally {
  if (child.exitCode === null) {
    child.kill("SIGTERM");
    await Promise.race([
      new Promise((resolve) => child.once("exit", resolve)),
      new Promise((resolve) => setTimeout(resolve, 5_000)),
    ]);
  }
}

console.log(`Production landing smoke test passed at ${url}`);
