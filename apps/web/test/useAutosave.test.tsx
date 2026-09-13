import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAutosave } from "@/lib/useAutosave";

/** A save that the test controls: each call returns a promise the test
 *  resolves or rejects when it chooses, so ordering can be asserted rather
 *  than hoped for. */
function controllableSave() {
  const calls: string[] = [];
  const settlers: Array<{ resolve: () => void; reject: (e?: unknown) => void }> = [];
  const onSave = vi.fn(async (value: string) => {
    calls.push(value);
    await new Promise<void>((resolve, reject) => settlers.push({ resolve, reject }));
  });
  return { onSave, calls, settlers };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("useAutosave", () => {
  it("does not save the value it was mounted with", async () => {
    // Opening a Meld that already has text must not bump its updated_at.
    const { onSave } = controllableSave();
    renderHook(() => useAutosave({ value: "loaded text", onSave, delayMs: 800 }));
    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(onSave).not.toHaveBeenCalled();
  });

  it("collapses a burst of edits into one save", async () => {
    const { onSave, calls, settlers } = controllableSave();
    const { rerender } = renderHook(
      ({ value }) => useAutosave({ value, onSave, delayMs: 800 }),
      { initialProps: { value: "" } },
    );

    for (const value of ["a", "ab", "abc", "abcd"]) {
      rerender({ value });
      await act(() => vi.advanceTimersByTimeAsync(100));
    }
    await act(() => vi.advanceTimersByTimeAsync(800));

    expect(calls).toEqual(["abcd"]);
    act(() => settlers[0].resolve());
  });

  it("reports saved only once the save resolves", async () => {
    const { onSave, settlers } = controllableSave();
    const { result, rerender } = renderHook(
      ({ value }) => useAutosave({ value, onSave, delayMs: 800 }),
      { initialProps: { value: "" } },
    );

    rerender({ value: "a" });
    expect(result.current.status).toBe("unsaved");

    await act(() => vi.advanceTimersByTimeAsync(800));
    expect(result.current.status).toBe("saving");

    await act(async () => settlers[0].resolve());
    expect(result.current.status).toBe("saved");
  });

  it("stays in saving while newer text is still queued", async () => {
    // Defect 7: claiming "saved" here would un-arm the close warning while
    // an older value was still in flight.
    const { onSave, settlers } = controllableSave();
    const { result, rerender } = renderHook(
      ({ value }) => useAutosave({ value, onSave, delayMs: 800 }),
      { initialProps: { value: "" } },
    );

    rerender({ value: "first" });
    await act(() => vi.advanceTimersByTimeAsync(800));
    rerender({ value: "second" });
    await act(() => vi.advanceTimersByTimeAsync(800));

    await act(async () => settlers[0].resolve());
    expect(result.current.status).not.toBe("saved");

    await act(async () => settlers[1].resolve());
    expect(result.current.status).toBe("saved");
  });

  it("sends queued saves in the order they were issued", async () => {
    // Without the single promise chain the server lands on whichever
    // request happens to arrive last, which may be the older text.
    const { onSave, calls, settlers } = controllableSave();
    const { rerender } = renderHook(
      ({ value }) => useAutosave({ value, onSave, delayMs: 100 }),
      { initialProps: { value: "" } },
    );

    rerender({ value: "first" });
    await act(() => vi.advanceTimersByTimeAsync(100));
    rerender({ value: "second" });
    await act(() => vi.advanceTimersByTimeAsync(100));

    expect(calls).toEqual(["first"]);
    await act(async () => settlers[0].resolve());
    expect(calls).toEqual(["first", "second"]);
    act(() => settlers[1].resolve());
  });

  it("flushes text typed inside the debounce window on unmount", async () => {
    // A client-side navigation fires no beforeunload, so without the
    // unmount flush this text disappears without a trace.
    const { onSave, calls, settlers } = controllableSave();
    const { rerender, unmount } = renderHook(
      ({ value }) => useAutosave({ value, onSave, delayMs: 800 }),
      { initialProps: { value: "" } },
    );

    rerender({ value: "typed just before leaving" });
    await act(() => vi.advanceTimersByTimeAsync(200));
    unmount();
    await act(() => vi.advanceTimersByTimeAsync(0));

    expect(calls).toEqual(["typed just before leaving"]);
    act(() => settlers[0].resolve());
  });

  it("does not flush on unmount when nothing is pending", async () => {
    const { onSave, calls, settlers } = controllableSave();
    const { rerender, unmount } = renderHook(
      ({ value }) => useAutosave({ value, onSave, delayMs: 100 }),
      { initialProps: { value: "" } },
    );

    rerender({ value: "saved already" });
    await act(() => vi.advanceTimersByTimeAsync(100));
    await act(async () => settlers[0].resolve());

    unmount();
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(calls).toEqual(["saved already"]);
  });

  it("reports error when a save fails", async () => {
    const { onSave, settlers } = controllableSave();
    const { result, rerender } = renderHook(
      ({ value }) => useAutosave({ value, onSave, delayMs: 100 }),
      { initialProps: { value: "" } },
    );

    rerender({ value: "a" });
    await act(() => vi.advanceTimersByTimeAsync(100));
    await act(async () => settlers[0].reject(new Error("offline")));

    expect(result.current.status).toBe("error");
  });

  it("does not claim saved after a failure followed by an identical edit", async () => {
    // Defect 8, and the one my own fix prescription introduced: the failure
    // reset must be guarded so a queued successor is not forgotten.
    const { onSave, calls, settlers } = controllableSave();
    const { result, rerender } = renderHook(
      ({ value }) => useAutosave({ value, onSave, delayMs: 100 }),
      { initialProps: { value: "" } },
    );

    rerender({ value: "a" });
    await act(() => vi.advanceTimersByTimeAsync(100));
    rerender({ value: "b" });
    await act(() => vi.advanceTimersByTimeAsync(100));

    // The first save fails while the second is already queued behind it.
    await act(async () => settlers[0].reject(new Error("offline")));
    expect(result.current.status).toBe("error");

    await act(async () => settlers[1].resolve());
    expect(calls).toEqual(["a", "b"]);
    expect(result.current.status).toBe("saved");
  });

  it("reports saved when an edit is undone back to the stored value", async () => {
    const { onSave, calls } = controllableSave();
    const { result, rerender } = renderHook(
      ({ value }) => useAutosave({ value, onSave, delayMs: 800 }),
      { initialProps: { value: "original" } },
    );

    rerender({ value: "original!" });
    await act(() => vi.advanceTimersByTimeAsync(200));
    rerender({ value: "original" });
    await act(() => vi.advanceTimersByTimeAsync(2000));

    // Nothing was ever sent, and the hook must not keep nagging on close.
    expect(calls).toEqual([]);
    expect(result.current.status).toBe("saved");
  });

  it("saves nothing until enabled becomes true", async () => {
    // Without the gate, the real value arriving one render after the
    // placeholder looks like a user edit and is saved straight back.
    const { onSave, calls } = controllableSave();
    const { rerender } = renderHook(
      ({ value, enabled }) => useAutosave({ value, onSave, delayMs: 100, enabled }),
      { initialProps: { value: "", enabled: false } },
    );

    rerender({ value: "loaded from the server", enabled: false });
    await act(() => vi.advanceTimersByTimeAsync(1000));
    rerender({ value: "loaded from the server", enabled: true });
    await act(() => vi.advanceTimersByTimeAsync(1000));

    expect(calls).toEqual([]);
  });
});
