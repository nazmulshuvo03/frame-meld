"use client";

export function QuoteButton({
  credits,
  balance,
  busy,
  busyLabel,
  label,
  onClick,
  disabled = false,
  flush = false,
}: {
  credits: number;
  balance: number | null;
  busy: boolean;
  busyLabel: string;
  label: string;
  onClick: () => void;
  /** Not allowed yet, as opposed to `busy`, which is "already working".
   *  The two look the same to a pointer and read very differently: a
   *  button that says "Queued…" when nothing is queued is a lie about
   *  what the app is doing, and the user cannot tell what to fix. */
  disabled?: boolean;
  /** One cell of a row ruled into a card's bottom edge, rather than a
   *  free-standing button. */
  flush?: boolean;
}) {
  // Unknown balance is not the same as an insufficient one: the balance
  // request may simply not have landed yet, and disabling the button on a
  // null would make the page look broken for a moment on every load.
  const affordable = balance === null || balance >= credits;

  return (
    <div className={`flex flex-col gap-1 ${flush ? "items-stretch" : "items-end"}`}>
      <button
        onClick={onClick}
        disabled={busy || disabled || !affordable}
        className={flush ? "btn btn-flush" : "btn"}
      >
        {busy ? busyLabel : `${label} · ${credits} credits`}
      </button>
      {!affordable && (
        <span role="alert" className={`text-xs text-danger ${flush ? "px-2.5 pb-2" : ""}`}>
          You need {credits - (balance ?? 0)} more credits.
        </span>
      )}
    </div>
  );
}
