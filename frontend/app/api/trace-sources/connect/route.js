import { NextResponse } from "next/server";

const SOURCES = new Set(["langsmith", "langfuse", "braintrust", "helicone", "opentelemetry", "custom"]);

export async function POST(request) {
  try {
    const body = await request.json();
    const source = String(body.source || "").trim().toLowerCase();
    const project = String(body.project || "").trim();
    const credential = String(body.credential || "").trim();
    if (!SOURCES.has(source)) return NextResponse.json({ error: "Unsupported trace source" }, { status: 400 });
    if (!project) return NextResponse.json({ error: "Project or workspace is required" }, { status: 400 });
    if (source !== "opentelemetry" && !credential) return NextResponse.json({ error: "A read-only credential is required" }, { status: 400 });
    return NextResponse.json({ status: "configured", connection: { source, project: project.slice(0, 256), privacy_mode: body.metadata_only === false ? "content" : "metadata_only", sdk: { schema_version: "1.0", trace_processor: "PromptRailSpanProcessor" } }, message: "Configuration validated. Remote synchronization remains pending until the connector is deployed." }, { status: 202 });
  } catch {
    return NextResponse.json({ error: "Request must contain valid JSON" }, { status: 400 });
  }
}
