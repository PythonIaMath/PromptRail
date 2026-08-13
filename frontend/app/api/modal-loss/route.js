import { spawn } from "node:child_process";
import { auth } from "../../lib/auth.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function getSession(request) {
  return auth.api.getSession({
    headers: request.headers,
  });
}

function parseLerouterLine(line) {
  const match = line.match(/LEROUTER_([A-Z0-9_]+)\s+({.*})/);
  if (!match) {
    return null;
  }

  try {
    const payload = JSON.parse(match[2]);
    const eventName = match[1].toLowerCase();
    return {
      event: eventName,
      payload,
    };
  } catch (error) {
    return {
      event: "parse_error",
      payload: {
        error: error.message,
        line,
      },
    };
  }
}

function isSafeAppIdentifier(value) {
  return /^[A-Za-z0-9_.-]{3,120}$/.test(value);
}

export async function GET(request) {
  const session = await getSession(request);
  if (!session?.user) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { searchParams } = new URL(request.url);
  const app = String(searchParams.get("app") || "").trim();

  if (!app || !isSafeAppIdentifier(app)) {
    return Response.json(
      { error: "Invalid Modal app id or app name." },
      { status: 400 },
    );
  }

  if (app === "demo") {
    const responseStream = new ReadableStream({
      start(controller) {
        const write = (event, data) => {
          controller.enqueue(
            new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`),
          );
        };

        let tick = 0;
        write("status", { state: "connecting", app });
        write("label_start", {
          event: "label_start",
          ts: new Date().toISOString(),
          run_id: "demo",
          total: 24,
          workers: 4,
        });

        const timer = setInterval(() => {
          tick += 1;
          if (tick <= 24) {
            write("label", {
              event: "label",
              ts: new Date().toISOString(),
              run_id: "demo",
              completed: tick,
              total: 24,
              route: tick % 3 === 0 ? "math_reasoning" : "general_reasoning",
              model_id: tick % 2 === 0 ? "openai/gpt-5.5" : "google/gemini-3.1-pro-preview",
              quality_score: Number((Math.sin(tick / 2) * 0.5 + 0.35).toFixed(2)),
              correct: tick % 4 !== 0,
            });
          } else if (tick <= 36) {
            const step = tick - 24;
            write("loss", {
              event: "train_loss",
              ts: new Date().toISOString(),
              run_id: "demo",
              step,
              epoch: step / 12,
              loss: Number((0.44 / Math.sqrt(step) + Math.random() * 0.035).toFixed(4)),
              learning_rate: 0.00002,
            });
          } else {
            clearInterval(timer);
            write("status", { state: "closed", code: 0, signal: null });
            controller.close();
          }
        }, 220);
      },
    });

    return new Response(responseStream, {
      headers: {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache, no-transform",
        Connection: "keep-alive",
      },
    });
  }

  let processRef;
  let closed = false;

  const responseStream = new ReadableStream({
    start(controller) {
      const write = (text) => controller.enqueue(new TextEncoder().encode(text));
      const emit = (event, data) => {
        write(`event: ${event}\n`);
        write(`data: ${JSON.stringify(data)}\n\n`);
      };

      emit("status", { state: "connecting", app });

      processRef = spawn("modal", ["app", "logs", "--timestamps", app], {
        stdio: ["ignore", "pipe", "pipe"],
        env: process.env,
      });

      let stdoutBuffer = "";
      let stderrBuffer = "";

      processRef.stdout.on("data", (chunk) => {
        stdoutBuffer += chunk.toString("utf8");
        const lines = stdoutBuffer.split(/\r?\n/);
        stdoutBuffer = lines.pop() || "";

        for (const line of lines) {
          const parsed = parseLerouterLine(line);
          if (parsed) {
            emit(parsed.event, parsed.payload);
          }
        }
      });

      processRef.stderr.on("data", (chunk) => {
        stderrBuffer += chunk.toString("utf8");
        const lines = stderrBuffer.split(/\r?\n/);
        stderrBuffer = lines.pop() || "";

        for (const line of lines) {
          if (line.trim()) {
            emit("stderr", { line });
          }
        }
      });

      processRef.on("error", (error) => {
        emit("error", { error: error.message });
        if (!closed) {
          closed = true;
          controller.close();
        }
      });

      processRef.on("close", (code, signal) => {
        if (stdoutBuffer.trim()) {
          const parsed = parseLerouterLine(stdoutBuffer);
          if (parsed) {
            emit(parsed.event, parsed.payload);
          }
        }

        emit("status", {
          state: "closed",
          code,
          signal,
        });

        if (!closed) {
          closed = true;
          controller.close();
        }
      });
    },
    cancel() {
      closed = true;
      if (processRef && !processRef.killed) {
        processRef.kill("SIGTERM");
      }
    },
  });

  return new Response(responseStream, {
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}
