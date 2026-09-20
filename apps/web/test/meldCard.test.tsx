import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MeldCard } from "@/components/MeldCard";
import type { MeldSummary } from "@/lib/api";

function meld(overrides: Partial<MeldSummary> = {}): MeldSummary {
  return {
    id: "m1",
    title: "The Lighthouse Keeper",
    stage: "cast",
    status: "draft",
    aspect_ratio: "16:9",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-20T00:00:00Z",
    ...overrides,
  };
}

const noop = vi.fn();

describe("MeldCard", () => {
  it("fills one segment per stage reached and leaves the rest empty", () => {
    const { container } = render(<MeldCard meld={meld()} onRename={noop} onDelete={noop} />);
    const segments = [...container.querySelectorAll("span.flex-1")];
    expect(segments).toHaveLength(5);
    // story, script, cast filled; shots and film not.
    expect(segments.map((s) => s.className.includes("bg-signal"))).toEqual([
      true,
      true,
      true,
      false,
      false,
    ]);
  });

  it("names a stage it does not recognise rather than showing nothing", () => {
    // The stage enum is the backend's, and a value added there before the
    // web app knows about it must not blank the card's only status line.
    render(<MeldCard meld={meld({ stage: "colour-grade" })} onRename={noop} onDelete={noop} />);
    expect(screen.getByText("colour-grade")).toBeInTheDocument();
  });
});
