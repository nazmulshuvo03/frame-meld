"use client";

/** Move up, move down, delete — the three controls every ordered row on the
 *  Script and Cast pages needs.
 *
 *  One component rather than three copies, because they have to behave
 *  identically: the same disabled rules at the ends of a list, the same
 *  confirmation before something generated is thrown away, and the same
 *  accessible names. A delete button that says only "×" is a button a screen
 *  reader announces as nothing.
 */
export function RowControls({
  label,
  onMove,
  onDelete,
  canMoveUp,
  canMoveDown,
  confirmMessage,
  busy = false,
}: {
  /** What this row is, for the buttons' accessible names: "scene 2". */
  label: string;
  /** Omitted for rows whose order is not the user's to choose. */
  onMove?: (delta: -1 | 1) => void;
  onDelete: () => void;
  canMoveUp?: boolean;
  canMoveDown?: boolean;
  /** Shown in a confirm() before deleting. Deleting a shot destroys a still
   *  and an audio clip the user paid for, and there is no undo — the ledger
   *  is append-only and the bytes are gone. */
  confirmMessage: string;
  busy?: boolean;
}) {
  const button = "rounded border border-subtle px-2 py-1 text-xs disabled:opacity-40";

  return (
    <div className="flex shrink-0 items-center gap-1">
      {onMove && (
        <>
          <button
            type="button"
            onClick={() => onMove(-1)}
            disabled={busy || !canMoveUp}
            aria-label={`Move ${label} up`}
            className={button}
          >
            ↑
          </button>
          <button
            type="button"
            onClick={() => onMove(1)}
            disabled={busy || !canMoveDown}
            aria-label={`Move ${label} down`}
            className={button}
          >
            ↓
          </button>
        </>
      )}
      <button
        type="button"
        // window.confirm rather than a modal: this is destructive and
        // irreversible, and a dialog component would be a dependency and a
        // focus-trap bug for one sentence of text.
        onClick={() => {
          if (window.confirm(confirmMessage)) onDelete();
        }}
        disabled={busy}
        aria-label={`Delete ${label}`}
        className={`${button} text-danger`}
      >
        Delete
      </button>
    </div>
  );
}
