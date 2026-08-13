import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";


test("hosted privacy disclosure names Infinite's request and retention boundaries", async () => {
  const source = (await readFile(new URL("./page.js", import.meta.url), "utf8"))
    .replace(/\s+/g, " ");

  for (const requiredDisclosure of [
    "PromptRail Infinite is a hosted inference service",
    "processes raw prompts, responses, attachments, and tool",
    "PromptRail does not write that raw content",
    "User-connected",
    "encrypted at rest",
    "sanitized execution receipts",
    "expire after 90 days",
    "/plugins/privacy",
  ]) {
    assert.ok(source.includes(requiredDisclosure), `missing disclosure: ${requiredDisclosure}`);
  }
});
