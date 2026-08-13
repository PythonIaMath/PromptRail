import { cp, mkdir, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = path.join(frontendRoot, "packages", "promptrail-cli");
const destination = path.join(frontendRoot, "app", "lib", "promptrail-cli");

await rm(destination, { recursive: true, force: true });
await mkdir(path.dirname(destination), { recursive: true });
await cp(source, destination, {
  recursive: true,
  filter: (entry) => !entry.includes(`${path.sep}test${path.sep}`),
});
