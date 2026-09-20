"use client";

import { useState } from "react";
import Link from "next/link";
import type { MeldSummary } from "@/lib/api";

/** The five stages in the order a Meld passes through them.
 *
 *  The same five the transport bar lists, and in the same order, because
 *  they are the same five states of the same artifact — a card that ordered
 *  them differently would teach the wrong shape of the product before the
 *  user ever opens one.
 */
const STAGES = [
  { id: "story", label: "Story" },
  { id: "script", label: "Script" },
  { id: "cast", label: "Cast" },
  { id: "shots", label: "Shots" },
  { id: "film", label: "Film" },
] as const;

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

  // How far this Meld has got. `stage` is a single word on the wire, so this
  // is the only thing a card can honestly say: reached, not "finished" and
  // not a count of what is inside.
  const reached = STAGES.findIndex((stage) => stage.id === meld.stage);
  const current = STAGES[reached] ?? null;

  return (
    <li className="relative flex flex-col rounded-lg border border-subtle bg-panel transition-colors hover:border-strong">
      <div className="flex-1 p-5">
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
            className="w-full rounded border border-strong px-2 py-1 text-lg font-medium focus:border-edge-focus focus:outline-none"
          />
        ) : (
          // The whole card face is the target, not just the words: a list of
          // films is clicked at, and a 16px-tall hit area in a 200px-wide
          // card is a mouse-accuracy test.
          <Link
            href={`/meld/${meld.id}`}
            className={
              "block truncate text-lg font-medium after:absolute after:inset-0 after:content-['']" +
              " focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-edge-focus"
            }
          >
            {meld.title}
          </Link>
        )}

        {/* The five stages as the transport bar draws them, so the card and
            the studio agree about what a filled dot means. Filled up to the
            stage this Meld has reached, hollow after it. */}
        <div className="mt-4 flex items-center gap-1.5" aria-hidden>
          {STAGES.map((stage, index) => (
            <span
              key={stage.id}
              className={`h-1 flex-1 rounded-full ${
                index <= reached ? "bg-signal" : "bg-subtle ring-1 ring-strong/40"
              }`}
            />
          ))}
        </div>

        <p className="mt-2.5 flex items-center gap-2 text-xs text-muted">
          <span>{current ? current.label : meld.stage}</span>
          <span className="text-faint" aria-hidden>
            ·
          </span>
          <span className="font-mono tabular-nums text-faint">{meld.aspect_ratio}</span>
          <span className="ml-auto font-mono tabular-nums text-faint">
            {new Date(meld.updated_at).toLocaleDateString()}
          </span>
        </p>
      </div>

      {/* Rename and Delete sit below the card's own edge rather than beside
          the title: they act on the Meld, they are not part of reading it.
          Kept in the document at all times — revealing them on hover alone
          would put them out of reach of a keyboard and of every touch
          screen. Delete stays quiet until it is pointed at; permanent red
          gives a destructive action the loudest colour on a page where
          nothing is wrong. */}
      <div className="relative z-10 flex gap-4 border-t border-subtle px-5 py-2.5 text-xs">
        <button
          onClick={() => {
            // Reset the draft before opening. Without this, a rename that
            // failed to persist reappears here as though it had saved.
            setDraftTitle(meld.title);
            setEditing(true);
          }}
          className="text-muted hover:text-foreground"
        >
          Rename
        </button>
        <button
          onClick={() => onDelete(meld.id)}
          className="text-muted hover:text-danger focus-visible:text-danger"
        >
          Delete
        </button>
      </div>
    </li>
  );
}
