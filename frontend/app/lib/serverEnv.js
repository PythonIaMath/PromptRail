import fs from "node:fs";
import path from "node:path";

let fileEnvCache;

function gitCommonDirectory(startDirectory) {
  let directory = path.resolve(startDirectory);

  while (true) {
    const gitPath = path.join(directory, ".git");
    if (fs.existsSync(gitPath)) {
      const stat = fs.statSync(gitPath);
      if (stat.isDirectory()) {
        return gitPath;
      }

      const match = fs.readFileSync(gitPath, "utf8").trim().match(/^gitdir:\s*(.+)$/);
      if (match) {
        const gitDirectory = path.resolve(directory, match[1]);
        const commonDirectoryPath = path.join(gitDirectory, "commondir");
        return fs.existsSync(commonDirectoryPath)
          ? path.resolve(gitDirectory, fs.readFileSync(commonDirectoryPath, "utf8").trim())
          : gitDirectory;
      }
    }

    const parent = path.dirname(directory);
    if (parent === directory) {
      return null;
    }
    directory = parent;
  }
}

function primaryWorktreeRoot(startDirectory) {
  const commonDirectory = gitCommonDirectory(startDirectory);
  return commonDirectory ? path.dirname(commonDirectory) : null;
}

export function environmentFilePaths(cwd = process.cwd()) {
  const projectRoot = path.basename(cwd) === "frontend" ? path.dirname(cwd) : cwd;
  const primaryRoot = primaryWorktreeRoot(projectRoot);
  const paths = [
    primaryRoot ? path.join(primaryRoot, ".env") : null,
    primaryRoot ? path.join(primaryRoot, "frontend", ".env.local") : null,
    path.join(projectRoot, ".env"),
    path.join(cwd, ".env"),
    path.join(cwd, ".env.local"),
  ].filter(Boolean);

  const uniquePaths = [];
  const seen = new Set();
  for (const filePath of paths.toReversed()) {
    if (!seen.has(filePath)) {
      seen.add(filePath);
      uniquePaths.push(filePath);
    }
  }
  return uniquePaths.reverse();
}

function parseEnvFile(filePath) {
  if (!fs.existsSync(filePath)) {
    return {};
  }

  const values = {};
  for (const line of fs.readFileSync(filePath, "utf8").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) {
      continue;
    }

    const [rawKey, ...rest] = trimmed.split("=");
    const key = rawKey.trim();
    let value = rest.join("=").trim();
    if (
      (value.startsWith('"') && value.endsWith('"'))
      || (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    } else {
      value = value.replace(/\s+#.*$/, "").trim();
    }
    values[key] = value;
  }
  return values;
}

function fileEnv() {
  if (!fileEnvCache) {
    fileEnvCache = environmentFilePaths()
      .reduce((acc, filePath) => ({ ...acc, ...parseEnvFile(filePath) }), {});
  }
  return fileEnvCache;
}

export function serverEnv(key, fallback = "") {
  return process.env[key] || fileEnv()[key] || fallback;
}
