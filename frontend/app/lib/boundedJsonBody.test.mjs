import assert from "node:assert/strict";
import test from "node:test";

import {
  BoundedJsonBodyError,
  boundedJsonErrorResponse,
  readBoundedJson,
} from "./boundedJsonBody.js";

test("reads a JSON body within its byte limit", async () => {
  const request = new Request("https://example.test/internal", {
    method: "POST",
    body: JSON.stringify({ ok: true }),
  });

  assert.deepEqual(await readBoundedJson(request, { maxBytes: 64 }), { ok: true });
});

test("rejects a declared request body larger than the route limit", async () => {
  const request = new Request("https://example.test/internal", {
    method: "POST",
    headers: { "content-length": "4096" },
    body: "{}",
  });

  await assert.rejects(
    () => readBoundedJson(request, { maxBytes: 1024 }),
    (error) => error instanceof BoundedJsonBodyError
      && error.code === "request_body_too_large"
      && error.status === 413,
  );
});

test("enforces the actual streamed byte count when content length is absent", async () => {
  const request = new Request("https://example.test/internal", {
    method: "POST",
    body: JSON.stringify({ value: "x".repeat(128) }),
  });

  await assert.rejects(
    () => readBoundedJson(request, { maxBytes: 32 }),
    (error) => error instanceof BoundedJsonBodyError
      && error.code === "request_body_too_large",
  );
});

test("returns a sanitized response for malformed JSON", async () => {
  const request = new Request("https://example.test/internal", {
    method: "POST",
    body: '{"secret":"must-not-appear"',
  });

  let caught;
  await assert.rejects(
    () => readBoundedJson(request, { maxBytes: 1024 }),
    (error) => {
      caught = error;
      return error.code === "invalid_json_body";
    },
  );
  const response = boundedJsonErrorResponse(caught);
  const text = await response.text();

  assert.equal(response.status, 400);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal(text.includes("must-not-appear"), false);
});
