import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RowControls } from "@/components/RowControls";

const base = {
  label: "shot 2",
  onDelete: vi.fn(),
  confirmMessage: "Delete shot 2?",
};

afterEach(() => vi.restoreAllMocks());

describe("RowControls", () => {
  it("names every button after the row it acts on", () => {
    // A delete button whose only label is "Delete" is announced identically
    // on every row in the list.
    render(<RowControls {...base} onMove={vi.fn()} canMoveUp canMoveDown />);
    expect(screen.getByRole("button", { name: "Move shot 2 up" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Move shot 2 down" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete shot 2" })).toBeInTheDocument();
  });

  it("disables the move that would go off the end of the list", () => {
    render(<RowControls {...base} onMove={vi.fn()} canMoveUp={false} canMoveDown />);
    expect(screen.getByRole("button", { name: /up/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /down/ })).toBeEnabled();
  });

  it("offers no move controls at all when order is not the user's to choose", () => {
    // A character's order_index decides the order their sheet is attached to
    // an image call, so reordering the cast would invalidate stills.
    render(<RowControls {...base} />);
    expect(screen.queryByRole("button", { name: /Move/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Delete/ })).toBeInTheDocument();
  });

  it("moves in the direction asked", () => {
    const onMove = vi.fn();
    render(<RowControls {...base} onMove={onMove} canMoveUp canMoveDown />);

    fireEvent.click(screen.getByRole("button", { name: /up/ }));
    expect(onMove).toHaveBeenCalledWith(-1);
    fireEvent.click(screen.getByRole("button", { name: /down/ }));
    expect(onMove).toHaveBeenCalledWith(1);
  });

  it("confirms before deleting, and does nothing when refused", () => {
    // There is no undo: the ledger is append-only and the bytes are gone.
    const onDelete = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<RowControls {...base} onDelete={onDelete} />);

    fireEvent.click(screen.getByRole("button", { name: /Delete/ }));
    expect(window.confirm).toHaveBeenCalledWith("Delete shot 2?");
    expect(onDelete).not.toHaveBeenCalled();
  });

  it("deletes once confirmed", () => {
    const onDelete = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<RowControls {...base} onDelete={onDelete} />);

    fireEvent.click(screen.getByRole("button", { name: /Delete/ }));
    expect(onDelete).toHaveBeenCalledTimes(1);
  });

  it("goes quiet while something else is in flight", () => {
    render(<RowControls {...base} onMove={vi.fn()} canMoveUp canMoveDown busy />);
    for (const button of screen.getAllByRole("button")) expect(button).toBeDisabled();
  });
});
