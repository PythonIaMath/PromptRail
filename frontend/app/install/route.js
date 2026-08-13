export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request) {
  const origin = new URL(request.url).origin;
  const packageUrl = `${origin}/api/cli/package`;
  const body = `#!/bin/sh
set -eu

if ! command -v node >/dev/null 2>&1; then
  echo "PromptRail requires Node.js 20 or newer." >&2
  exit 1
fi

major="$(node -p 'Number(process.versions.node.split(".")[0])')"
if [ "$major" -lt 20 ]; then
  echo "PromptRail requires Node.js 20 or newer." >&2
  exit 1
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT INT TERM

curl -fsSL ${JSON.stringify(packageUrl)} -o "$tmp_dir/promptrail-cli.tar.gz"
tar -xzf "$tmp_dir/promptrail-cli.tar.gz" -C "$tmp_dir"
exec node "$tmp_dir/promptrail-cli/bin/promptrail.mjs" install "$@"
`;
  return new Response(body, {
    headers: {
      "content-type": "text/x-shellscript; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}
