import assert from "node:assert/strict";
import test from "node:test";

import { buildInfiniteAccessSnapshot } from "./infiniteAccess.js";

test("Infinite access requires an active entitlement with inference scope", () => {
  assert.deepEqual(buildInfiniteAccessSnapshot([]), {
    allowed: false,
    entitlements: [],
    scopes: [],
    beta: false,
  });
  assert.equal(buildInfiniteAccessSnapshot([
    { product: "infinite_beta", status: "inactive", scopes: ["infinite:infer"] },
  ]).allowed, false);
  assert.equal(buildInfiniteAccessSnapshot([
    { product: "infinite_beta", status: "active", scopes: [] },
  ]).allowed, false);
});

test("Infinite access preserves explicit scopes", () => {
  const access = buildInfiniteAccessSnapshot([
    {
      product: "infinite_beta",
      status: "trialing",
      scopes: ["usage:read", "infinite:infer", "providers:connect"],
    },
  ]);
  assert.equal(access.allowed, true);
  assert.equal(access.beta, true);
  assert.deepEqual(access.entitlements, ["infinite_beta"]);
  assert.deepEqual(access.scopes, ["infinite:infer", "providers:connect", "usage:read"]);
});
