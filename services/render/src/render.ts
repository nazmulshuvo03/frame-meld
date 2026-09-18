import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { bundle } from "@remotion/bundler";
import { ensureBrowser, renderMedia, selectComposition } from "@remotion/renderer";
import type { SceneSpec } from "./types.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ENTRY = path.join(HERE, "remotion", "index.ts");
const COMPOSITION_ID = "Film";

/** Bundling the composition takes seconds and produces the same bundle
 *  every time. Cached as the in-flight promise rather than the result, so
 *  two renders arriving together share one bundle instead of racing to
 *  build two. */
let bundled: Promise<string> | null = null;

export function warmUp(): Promise<unknown> {
  // Both of the slow one-time costs, paid at startup rather than inside the
  // first render: the bundle above, and Remotion's headless Chrome, which
  // it downloads on first use. A ~150 MB download inside a request looks
  // exactly like a hung render from the outside.
  return Promise.all([getBundle(), ensureBrowser()]);
}

function getBundle(): Promise<string> {
  bundled ??= bundle({ entryPoint: ENTRY });
  return bundled;
}

export async function renderSpec(
  spec: SceneSpec,
  onProgress: (progress: number) => void,
): Promise<string> {
  const serveUrl = await getBundle();

  // selectComposition runs calculateMetadata with these exact props, so the
  // film's size and length come from the spec rather than from anything
  // declared here.
  const composition = await selectComposition({
    serveUrl,
    id: COMPOSITION_ID,
    inputProps: { spec },
  });

  const directory = await mkdtemp(path.join(tmpdir(), "framemeld-render-"));
  const outputLocation = path.join(directory, "film.mp4");

  await renderMedia({
    composition,
    serveUrl,
    codec: "h264",
    outputLocation,
    inputProps: { spec },
    onProgress: ({ progress }) => onProgress(progress),
  });

  return outputLocation;
}
