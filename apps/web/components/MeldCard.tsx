"use client";

import { useState } from "react";
import Link from "next/link";
import type { MeldSummary } from "@/lib/api";

const STAGE_LABELS: Record<string, string> = {
  story: "Story",
  script: "Script",
  cast: "Cast",
  shots: "Shots",
  film: "Film",
};

export function MeldCard({
  meld,
  onRename,
  onDelete,
}: {
  meld: MeldSummary;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState(meld.title);

  function commitRename() {
    const trimmed = draftTitle.trim();
    if (trimmed && trimmed !== meld.title) {
      onRename(meld.id, trimmed);
    } else {
      setDraftTitle(meld.title);
    }
    setEditing(false);
  }

  return (
    <li className="flex items-center justify-between rounded border border-subtle p-4">
      <div className="min-w-0">
        {editing ? (
          <input
            autoFocus
            aria-label="Meld title"
            value={draftTitle}
            onChange={(e) => setDraftTitle(e.target.value)}
            onBlur={commitRename}
            onKeyDown={(e) => {
              if (e.key === "Enter") commitRename();
              if (e.key === "Escape") {
                setDraftTitle(meld.title);
                setEditing(false);
              }
            }}
            className="rounded border border-strong px-2 py-1"
          />
        ) : (
          <Link href={`/meld/${meld.id}`} className="block truncate font-medium hover:underline">
            {meld.title}
          </Link>
        )}
        <p className="mt-1 text-xs text-muted">
          {STAGE_LABELS[meld.stage] ?? meld.stage} · updated{" "}
          {new Date(meld.updated_at).toLocaleDateString()}
        </p>
      </div>

      <div className="flex shrink-0 gap-3 text-sm">
        <button
          onClick={() => {
            // Reset the draft before opening. Without this, a rename that
            // failed to persist reappears here as though it had saved.
            setDraftTitle(meld.title);
            setEditing(true);
          }}
          className="text-body hover:underline"
        >
          Rename
        </button>
        <button onClick={() => onDelete(meld.id)} className="text-danger hover:underline">
          Delete
        </button>
      </div>
    </li>
  );
}
