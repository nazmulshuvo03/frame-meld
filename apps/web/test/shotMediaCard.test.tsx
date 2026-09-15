// fireEvent rather than user-event: this is one click, and adding a
// dependency to simulate it would cost more than it is worth.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ShotMediaCard } from "@/components/ShotMediaCard";
import type { ShotMedia } from "@/lib/api";

function shot(overrides: Partial<ShotMedia> = {}): ShotMedia {
  return {
    id: "shot-1",
    order_index: 0,
    image_prompt: "A woman reads a letter by a window.",
    motion_prompt: "Slow push in.",
    duration_sec: "4.00",
    camera: "medium",
    dialogue_kind: "none",
    speech_text: "",
    state: "fresh",
    content_hash: "c",
    updated_at: "2026-09-15T00:00:00Z",
    character_ids: [],
    character_names: [],
    still: null,
    audio: null,
    ...overrides,
  };
}

const base = {
  stillCredits: 12,
  speechCredits: 2,
  balance: 500,
  busy: false,
  onGenerateStill: vi.fn(),
  onGenerateSpeech: vi.fn(),
};

describe("ShotMediaCard", () => {
  it("names who is in the frame", () => {
    render(
      <ShotMediaCard
        {...base}
        shot={shot({ character_ids: ["a", "b"], character_names: ["Elena", "Marcus"] })}
      />,
    );
    expect(screen.getByText("Elena, Marcus")).toBeInTheDocument();
  });

  it("says so when nobody is in the frame", () => {
    // An insert or an establishing shot. Distinguishable from "not cast
    // yet", which the page banner covers — a card that just showed nothing
    // would leave the two looking identical.
    render(<ShotMediaCard {...base} shot={shot()} />);
    expect(screen.getByText("nobody in frame")).toBeInTheDocument();
  });

  it("shows the price on the button face before the click", () => {
    render(<ShotMediaCard {...base} shot={shot()} />);
    expect(screen.getByRole("button", { name: /Generate still/ })).toHaveTextContent(
      "12 credits",
    );
  });

  it("offers no audio button on a silent shot", () => {
    render(<ShotMediaCard {...base} shot={shot()} />);
    expect(screen.queryByRole("button", { name: /audio/i })).not.toBeInTheDocument();
  });

  it("offers an audio button once there is a line to read", () => {
    render(
      <ShotMediaCard
        {...base}
        shot={shot({ dialogue_kind: "narration", speech_text: "She had known for years." })}
      />,
    );
    expect(screen.getByRole("button", { name: /Generate audio/ })).toHaveTextContent(
      "2 credits",
    );
  });

  it("keeps showing a stale still rather than hiding it", () => {
    // Spec 4.1's promise: stale never means deleted, so reverting the
    // upstream edit restores the picture for free.
    render(
      <ShotMediaCard
        {...base}
        shot={shot({
          still: {
            id: "i",
            state: "stale",
            asset_id: "asset-1",
            ref_character_image_ids: [],
            credits_spent: 12,
            updated_at: "2026-09-15T00:00:00Z",
          },
        })}
      />,
    );
    expect(screen.getByRole("img")).toBeInTheDocument();
    expect(screen.getByText("Out of date")).toBeInTheDocument();
  });

  it("disables both buttons while the balance is too low", () => {
    render(
      <ShotMediaCard
        {...base}
        balance={1}
        shot={shot({ dialogue_kind: "narration", speech_text: "A line." })}
      />,
    );
    for (const button of screen.getAllByRole("button")) expect(button).toBeDisabled();
  });

  it("affordability is per action, not global", () => {
    // 3 credits buys the audio and not the still. Disabling both would tell
    // the user they cannot do something they can.
    render(
      <ShotMediaCard
        {...base}
        balance={3}
        shot={shot({ dialogue_kind: "narration", speech_text: "A line." })}
      />,
    );
    expect(screen.getByRole("button", { name: /Generate audio/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /Generate still/ })).toBeDisabled();
  });

  it("asks for the still when clicked", () => {
    const onGenerateStill = vi.fn();
    const subject = shot();
    render(<ShotMediaCard {...base} shot={subject} onGenerateStill={onGenerateStill} />);

    fireEvent.click(screen.getByRole("button", { name: /Generate still/ }));

    expect(onGenerateStill).toHaveBeenCalledWith(subject);
  });
});
