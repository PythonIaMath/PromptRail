import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const landingSource = readFileSync(
  new URL("../components/LandingPage.js", import.meta.url),
  "utf8",
);

test("the root landing component always renders the enterprise experience", () => {
  assert.match(landingSource, /<main className="landing-page landing-page-enterprise">/);
  assert.match(landingSource, /Save 70% on token costs/);
  assert.match(landingSource, /<BudgetPromiseSection enterprise \/>/);
  assert.match(landingSource, /<EnterpriseQuoteSection \/>/);
  assert.match(landingSource, /<FastSetupSection enterprise \/>/);

  assert.doesNotMatch(landingSource, /canShowEnterpriseMode/);
  assert.doesNotMatch(landingSource, /effectiveEnterpriseMode/);
  assert.doesNotMatch(landingSource, /process\.env\.NODE_ENV/);
});
