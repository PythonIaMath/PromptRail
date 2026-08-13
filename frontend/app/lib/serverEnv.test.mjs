import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { environmentFilePaths } from "./serverEnv.js";

test("discovers the primary checkout env files from a linked worktree", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "promptrail-env-"));
  const primaryRoot = path.join(root, "primary");
  const commonGitDirectory = path.join(primaryRoot, ".git");
  const worktreeRoot = path.join(root, "worktree");
  const worktreeGitDirectory = path.join(commonGitDirectory, "worktrees", "feature");
  const frontendDirectory = path.join(worktreeRoot, "frontend");

  fs.mkdirSync(worktreeGitDirectory, { recursive: true });
  fs.mkdirSync(frontendDirectory, { recursive: true });
  fs.writeFileSync(
    path.join(worktreeRoot, ".git"),
    `gitdir: ${worktreeGitDirectory}\n`,
  );
  fs.writeFileSync(
    path.join(worktreeGitDirectory, "commondir"),
    "../..\n",
  );

  assert.deepEqual(environmentFilePaths(frontendDirectory), [
    path.join(primaryRoot, ".env"),
    path.join(primaryRoot, "frontend", ".env.local"),
    path.join(worktreeRoot, ".env"),
    path.join(frontendDirectory, ".env"),
    path.join(frontendDirectory, ".env.local"),
  ]);
});

test("keeps local worktree env files after shared env files for overrides", () => {
  const paths = environmentFilePaths(process.cwd());

  assert.equal(paths.at(-2), path.join(process.cwd(), ".env"));
  assert.equal(paths.at(-1), path.join(process.cwd(), ".env.local"));
});
