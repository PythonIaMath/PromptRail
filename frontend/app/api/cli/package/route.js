import { createGzip } from "node:zlib";
import { PassThrough, Readable } from "node:stream";
import { spawn } from "node:child_process";
import path from "node:path";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  const integrationRoot = path.join(process.cwd(), "app", "lib", "promptrail-cli");
  const tar = spawn("tar", ["-cf", "-", "-C", path.dirname(integrationRoot), path.basename(integrationRoot)], {
    stdio: ["ignore", "pipe", "pipe"],
  });
  const gzip = createGzip({ level: 9 });
  const output = new PassThrough();
  let errorText = "";
  tar.stderr.on("data", (chunk) => {
    errorText += chunk.toString();
  });
  tar.on("error", (error) => output.destroy(error));
  tar.on("close", (code) => {
    if (code !== 0) {
      output.destroy(new Error(errorText || `tar exited with code ${code}`));
    }
  });
  tar.stdout.pipe(gzip).pipe(output);

  return new Response(Readable.toWeb(output), {
    headers: {
      "content-type": "application/gzip",
      "content-disposition": 'attachment; filename="promptrail-cli.tar.gz"',
      "cache-control": "no-store",
    },
  });
}
