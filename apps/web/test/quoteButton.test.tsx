import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { QuoteButton } from "@/components/QuoteButton";

const base = { credits: 12, busy: false, busyLabel: "Drawing…", label: "Generate sheet" };

describe("QuoteButton", () => {
  it("shows the price before the click", () => {
    render(<QuoteButton {...base} balance={100} onClick={vi.fn()} />);
    expect(screen.getByRole("button")).toHaveTextContent("12 credits");
  });

  it("refuses and says how short the user is", () => {
    render(<QuoteButton {...base} balance={5} onClick={vi.fn()} />);
    expect(screen.getByRole("button")).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("7 more credits");
  });

  it("allows a balance exactly equal to the price", () => {
    render(<QuoteButton {...base} balance={12} onClick={vi.fn()} />);
    expect(screen.getByRole("button")).toBeEnabled();
  });

  it("stays enabled while the balance is still unknown", () => {
    // Null is "not loaded yet", not "zero". Disabling on it would make the
    // page look broken for a moment on every load.
    render(<QuoteButton {...base} balance={null} onClick={vi.fn()} />);
    expect(screen.getByRole("button")).toBeEnabled();
  });

  it("shows the busy label and blocks a second click", () => {
    render(<QuoteButton {...base} busy balance={100} onClick={vi.fn()} />);
    expect(screen.getByRole("button")).toBeDisabled();
    expect(screen.getByRole("button")).toHaveTextContent("Drawing…");
  });
});
