import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import http from "node:http";
import { randomUUID } from "node:crypto";
import { renderSpec, warmUp } from "./render.ts";
import { validateSpec } from "./types.ts";

const PORT = Number(process.env.PORT ?? 8790);

/** How long a finished file stays fetchable. The worker downloads it
 *  within seconds of being told it is ready (M5-D9); this is only a bound
 *  on how much disk a crashed worker can strand. */
const KEEP_MS = 30 * 60 * 1000;

type Render = {
  status: "queued" | "rendering" | "done" | "failed";
  progress: number;
  file?: string;
  error?: string;
  finishedAt?: number;
};

/** In memory, deliberately. The service has no database (design spec §3.1)
 *  and a render that a restart forgets is a render the worker re-requests
 *  — which is cheaper to operate than any store that has to be kept true. */
const renders = new Map<string, Render>();

/** Renders run one at a time. Remotion saturates the machine's cores, so
 *  two concurrent films take longer than two sequential ones and make both
 *  users wait. A promise chain rather than a queue library: there is
 *  nothing to configure and nothing to monitor. */
let chain: Promise<unknown> = Promise.resolve();

function json(response: http.ServerResponse, status: number, body: unknown): void {
  const payload = JSON.stringify(body);
  response.writeHead(status, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(payload),
  });
  response.end(payload);
}

async function readBody(request: http.IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    // A scene spec for a 30-shot film is a few kilobytes. A megabyte is
    // already something other than a scene spec, and reading it into
    // memory before finding that out is how a service falls over.
    if (size > 1_000_000) throw new Error("The scene spec is too large.");
    chunks.push(chunk as Buffer);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function sweep(): void {
  const cutoff = Date.now() - KEEP_MS;
  for (const [id, render] of renders) {
    if (render.finishedAt !== undefined && render.finishedAt < cutoff) renders.delete(id);
  }
}

function start(spec: ReturnType<typeof validateSpec>): string {
  const id = randomUUID();
  renders.set(id, { status: "queued", progress: 0 });

  chain = chain.then(async () => {
    const render = renders.get(id);
    // Dropped by the sweep, or the process is shutting down. Nothing to
    // render for and nobody to tell.
    if (!render) return;
    render.status = "rendering";
    try {
      render.file = await renderSpec(spec, (progress) => {
        render.progress = progress;
      });
      render.status = "done";
      render.progress = 1;
    } catch (error) {
      render.status = "failed";
      // The message reaches a user through the job row, so it is the
      // error's own text and never a stack trace.
      render.error = error instanceof Error ? error.message : String(error);
    } finally {
      render.finishedAt = Date.now();
      sweep();
    }
  });

  return id;
}

async function serveOutput(response: http.ServerResponse, file: string): Promise<void> {
  const { size } = await stat(file);
  response.writeHead(200, { "Content-Type": "video/mp4", "Content-Length": size });
  createReadStream(file).pipe(response);
}

const server = http.createServer((request, response) => {
  void (async () => {
    const url = new URL(request.url ?? "/", `http://${request.headers.host ?? "localhost"}`);
    const path = url.pathname;

    if (request.method === "GET" && path === "/health") {
      return json(response, 200, { status: "ok" });
    }

    if (request.method === "POST" && path === "/render") {
      try {
        const spec = validateSpec(await readBody(request));
        return json(response, 202, { render_id: start(spec), status: "queued" });
      } catch (error) {
        // 400, not 500: a spec this service cannot read is the caller's
        // problem, and the worker treats 4xx as terminal rather than
        // retrying a document that will never parse.
        return json(response, 400, {
          error: error instanceof Error ? error.message : "Unreadable scene spec.",
        });
      }
    }

    const output = /^\/render\/([^/]+)\/output$/.exec(path);
    const status = /^\/render\/([^/]+)$/.exec(path);

    if (request.method === "GET" && (output || status)) {
      const render = renders.get((output ?? status)![1]);
      if (!render) return json(response, 404, { error: "No such render." });

      if (output) {
        if (render.status !== "done" || !render.file) {
          return json(response, 409, { error: "That render is not finished." });
        }
        return serveOutput(response, render.file);
      }

      return json(response, 200, {
        status: render.status,
        progress: render.progress,
        // Absolute, because the worker fetches it directly. Built from the
        // Host it was asked on, so the address that reached this service
        // is the address it hands back.
        output_url:
          render.status === "done"
            ? `${url.protocol}//${url.host}/render/${(output ?? status)![1]}/output`
            : undefined,
        error: render.error,
      });
    }

    return json(response, 404, { error: "Not found." });
  })().catch((error: unknown) => {
    if (!response.headersSent) json(response, 500, { error: String(error) });
  });
});

server.listen(PORT, () => {
  console.log(`render service listening on :${PORT}`);
  // Not awaited before listening: /health should answer while Chrome is
  // still downloading, so a supervisor can tell "starting" from "dead".
  warmUp().then(
    () => console.log("bundle and browser ready"),
    (error: unknown) => console.error("warm-up failed", error),
  );
});
