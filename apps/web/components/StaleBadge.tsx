export function StaleBadge({ className = "" }: { className?: string }) {
  return (
    <span
      className={`rounded-full bg-warn px-2 py-0.5 text-xs font-medium text-warn-foreground ${className}`}
      // Announced, because the badge appearing is how a user learns their
      // story edit invalidated work they cannot see on this screen.
      role="status"
    >
      Out of date
    </span>
  );
}
