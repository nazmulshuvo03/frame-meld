import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/** The defect this file exists for: every page used fixed `text-neutral-*`
 *  colours, so under a dark OS theme the app rendered dark-on-dark. Checking
 *  the rendered pixels would need a browser; checking the tokens they are all
 *  built from needs only arithmetic, and catches the same thing — a token
 *  pair that cannot be read is a page that cannot be read.
 */
const css = readFileSync(path.join(import.meta.dirname, "..", "app", "globals.css"), "utf8");

/** The `:root` block. Crude on purpose: the file is ours, it has exactly one
 *  such block, and a parser would be more machinery than the thing parsed. */
function tokens(): Record<string, string> {
  const block = css.slice(css.indexOf(":root"), css.indexOf("}", css.indexOf(":root")));
  return Object.fromEntries(
    [...block.matchAll(/--([\w-]+):\s*(#[0-9a-f]{3,8})/gi)].map((m) => [m[1], m[2]]),
  );
}

function channel(value: number): number {
  const c = value / 255;
  return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** Foreground/background pairs that actually occur in the app. */
const TEXT_PAIRS: [string, string, string][] = [
  ["foreground", "background", "body text on the page"],
  ["body", "background", "secondary paragraphs"],
  ["muted", "background", "captions and counts"],
  ["danger", "background", "error messages"],
  ["foreground", "panel", "text inside a panel"],
  ["body", "panel", "secondary text inside a panel"],
  ["muted", "panel", "captions inside a panel"],
  ["accent-foreground", "accent", "a primary button's label"],
  ["warn-foreground", "warn", "an out-of-date notice"],
];

/** UI components you have to be able to find: WCAG 1.4.11's 3:1. An input's
 *  border is its only boundary here, and a focus ring that nobody can see is
 *  a keyboard user with no cursor. */
const CONTROL_PAIRS: [string, string, string][] = [
  ["strong", "background", "an input's edge"],
  ["edge-focus", "background", "a focused input's edge"],
];

/** Decoration: edges that group content which position already groups, and
 *  placeholder text, which is a hint rather than content. These only have to
 *  be perceptible — holding a card border to 3:1 would draw a box around
 *  every section louder than the sections. */
const EDGE_PAIRS: [string, string, string][] = [
  ["subtle", "background", "a card edge"],
  ["warn-border", "warn", "an out-of-date notice's edge"],
  ["faint", "background", "placeholder text"],
];

describe("the theme", () => {
  const palette = tokens();

  it("is one light theme that ignores the OS dark setting", () => {
    expect(css).not.toContain("prefers-color-scheme: dark");
    expect(css).toContain("color-scheme: light");
  });

  it.each(TEXT_PAIRS)("reads %s on %s (%s)", (fg, bg) => {
    expect(palette[fg]).toBeDefined();
    expect(palette[bg]).toBeDefined();
    // WCAG AA for body text.
    expect(contrast(palette[fg], palette[bg])).toBeGreaterThanOrEqual(4.5);
  });

  it.each(CONTROL_PAIRS)("shows %s against %s (%s)", (fg, bg) => {
    expect(contrast(palette[fg], palette[bg])).toBeGreaterThanOrEqual(3);
  });

  it.each(EDGE_PAIRS)("keeps %s perceptible against %s (%s)", (fg, bg) => {
    expect(contrast(palette[fg], palette[bg])).toBeGreaterThanOrEqual(1.2);
  });

  it("keeps the page a light, neutral grey ground", () => {
    // The prototype's technical ground: light, and neither warm nor cool.
    // Bounds on the blue channel against red catch a drift into a tint.
    const [r, , b] = [1, 3, 5].map((i) => parseInt(palette.background.slice(i, i + 2), 16));
    expect(luminance(palette.background)).toBeGreaterThan(0.85);
    expect(Math.abs(r - b)).toBeLessThanOrEqual(6);
  });

  it("holds paragraph-size accent text and the current stage's tint to AA", () => {
    expect(contrast(palette["accent-ink"], palette.background)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(palette.foreground, palette["accent-tint"])).toBeGreaterThanOrEqual(4.5);
  });
});

/** Every .tsx under a directory. `readdirSync` with `recursive` rather than
 *  a glob helper, because node's own glob types are not stable across the
 *  versions this repo runs on and this is four lines. */
function walk(dir: string): string[] {
  return readdirSync(dir, { recursive: true, encoding: "utf8" })
    .filter((name) => name.endsWith(".tsx"))
    .map((name) => path.join(dir, name));
}

describe("the stylesheet", () => {
  it("leaves no raw Tailwind palette in the app's own components", () => {
    // The tokens only help if nothing bypasses them. This is the guard that
    // keeps the next component from reaching for text-neutral-500 again.
    const root = path.join(import.meta.dirname, "..");
    const files = [...walk(path.join(root, "app")), ...walk(path.join(root, "components"))];
    expect(files.length).toBeGreaterThan(0);

    const offenders = files.filter((file) => {
      const source = readFileSync(file, "utf8");
      // bg-black is allowed: it is the letterbox behind a video, and it is
      // correct in both themes.
      return /(?:text|bg|border|placeholder|ring|divide)-(?:neutral|amber|slate|gray|zinc|stone)-\d+|text-white\b/.test(
        source,
      );
    });
    expect(offenders).toEqual([]);
  });
});
