export function StaleBadge({ className = "" }: { className?: string }) {
  return (
    <span
      className={`inline-flex items-center border border-warn-border bg-background px-2.5 py-[3px] text-[11px] text-warn-foreground ${className}`}
      // Announced, because the badge appearing is how a user learns their
      // story edit invalidated work they cannot see on this screen.
      role="status"
    >
      Out of date
    </span>
  );
}
