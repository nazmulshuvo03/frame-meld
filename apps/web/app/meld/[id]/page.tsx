"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { getMeld, saveStory, type MeldDetail } from "@/lib/api";
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

  useEffect(() => {
    getMeld(id)
      .then((detail) => {
        setMeld(detail);
        setText(detail.story?.text ?? "");
        setLoaded(true);
      })
      .catch(() => router.replace("/melds"));
  }, [id, router]);

  const onSave = useCallback(
    async (value: string) => {
      await saveStory(id, value);
    },
    [id],
  );

  const { status } = useAutosave({ value: text, onSave, delayMs: 800 });

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

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Write your story here. Anything from a paragraph to a few pages — you'll turn it into a script next."
        className="min-h-[60vh] flex-1 resize-none rounded border border-neutral-200 p-6 font-serif text-lg leading-relaxed outline-none focus:border-neutral-400"
      />

      <footer className="mt-4 flex items-center justify-between text-sm text-neutral-500">
        <span>
          {wordCount} {wordCount === 1 ? "word" : "words"}
        </span>
        <span className="text-neutral-400">Next up: Script (arrives in M2)</span>
      </footer>
    </main>
  );
}
