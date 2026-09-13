"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ApiError, getMeld, saveStory, type MeldDetail } from "@/lib/api";
import { useAutosave, type SaveStatus } from "@/lib/useAutosave";

const STATUS_TEXT: Record<SaveStatus, string> = {
  idle: "",
  unsaved: "Unsaved changes",
  saving: "Saving…",
  saved: "Saved",
  error: "Couldn't save — check your connection",
};

export default function MeldEditorPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();

  const [meld, setMeld] = useState<MeldDetail | null>(null);
  const [text, setText] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  // The hash the server last confirmed. A ref, not state: changing it must
  // not re-render, and onSave must see the newest value rather than one
  // captured in a stale closure.
  const contentHashRef = useRef<string | undefined>(undefined);

  useEffect(() => {
    getMeld(id)
      .then((detail) => {
        setMeld(detail);
        setText(detail.story?.text ?? "");
        contentHashRef.current = detail.story?.content_hash;
        setLoaded(true);
      })
      .catch((err) => {
        // Only a 401 means the session is gone. A 404 means this Meld is
        // genuinely gone or not theirs, so bounce to the dashboard. Any
        // other failure — the API unreachable, a 500 — must not masquerade
        // as either, or a transient blip strands or misdirects a user whose
        // Meld is fine. Mirrors /melds's refresh() classification.
        if (err instanceof ApiError && err.status === 401) {
          router.replace("/login");
        } else if (err instanceof ApiError && err.status === 404) {
          router.replace("/melds");
        } else {
          setLoadError("Couldn't load this Meld. Check your connection and reload.");
        }
      });
  }, [id, router]);

  const onSave = useCallback(
    async (value: string) => {
      try {
        const saved = await saveStory(id, value, contentHashRef.current);
        contentHashRef.current = saved.content_hash;
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          // Someone else — almost always this user's own second tab — saved
          // in between. Stop editing rather than racing them: the textarea
          // goes read-only and the banner explains why. Rethrown so
          // useAutosave still reports "error" and keeps the close warning
          // armed, because this text really is unsaved.
          setConflict(true);
        }
        throw err;
      }
    },
    [id],
  );

  const { status } = useAutosave({ value: text, onSave, delayMs: 800, enabled: loaded });

  if (loadError) {
    return (
      <main className="p-8">
        <p role="alert" className="text-sm text-red-600">
          {loadError}
        </p>
      </main>
    );
  }

  if (!loaded || meld === null) {
    return <main className="p-8 text-sm text-neutral-500">Loading…</main>;
  }

  const wordCount = text.trim() ? text.trim().split(/\s+/).length : 0;

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col p-8">
      <header className="mb-6 flex items-baseline justify-between">
        <div>
          <Link href="/melds" className="text-sm text-neutral-500 hover:underline">
            ← My Melds
          </Link>
          <h1 className="mt-1 text-2xl font-semibold">{meld.title}</h1>
        </div>
        <span
          className={`text-sm ${status === "error" ? "text-red-600" : "text-neutral-500"}`}
          role="status"
          aria-live="polite"
        >
          {STATUS_TEXT[status]}
        </span>
      </header>

      {conflict && (
        <div
          role="alert"
          className="mb-4 rounded border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"
        >
          <strong className="font-medium">This Meld was edited somewhere else.</strong> Your
          changes here have not been saved. Copy anything you want to keep, then{" "}
          <button onClick={() => window.location.reload()} className="underline">
            reload
          </button>{" "}
          to get the latest version.
        </div>
      )}

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={conflict}
        placeholder="Write your story here. Anything from a paragraph to a few pages — you'll turn it into a script next."
        className="min-h-[60vh] flex-1 resize-none rounded border border-neutral-200 p-6 font-serif text-lg leading-relaxed outline-none focus:border-neutral-400 disabled:bg-neutral-50 disabled:text-neutral-500"
      />

      <footer className="mt-4 flex items-center justify-between text-sm text-neutral-500">
        <span>
          {wordCount} {wordCount === 1 ? "word" : "words"}
        </span>
        <Link href={`/meld/${id}/script`} className="text-neutral-500 hover:underline">
          Next up: Script →
        </Link>
      </footer>
    </main>
  );
}
