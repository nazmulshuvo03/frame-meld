"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { MeldCard } from "@/components/MeldCard";
import {
  createMeld,
  deleteMeld,
  listMelds,
  logout,
  renameMeld,
  type MeldSummary,
} from "@/lib/api";

export default function MeldsPage() {
  const router = useRouter();
  const [melds, setMelds] = useState<MeldSummary[] | null>(null);

  const refresh = useCallback(async () => {
    try {
      setMelds(await listMelds());
    } catch {
      router.replace("/login");
    }
  }, [router]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function onNewMeld() {
    const created = await createMeld();
    router.push(`/meld/${created.id}`);
  }

  async function onRename(id: string, title: string) {
    await renameMeld(id, title);
    await refresh();
  }

  async function onDelete(id: string) {
    if (!confirm("Delete this Meld? You can't undo this from the app.")) return;
    await deleteMeld(id);
    await refresh();
  }

  async function onLogout() {
    await logout();
    router.replace("/login");
  }

  return (
    <main className="mx-auto max-w-2xl p-8">
      <header className="mb-8 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">My Melds</h1>
        <div className="flex items-center gap-4">
          <button
            onClick={onNewMeld}
            className="rounded bg-neutral-900 px-4 py-2 text-sm text-white"
          >
            New Meld
          </button>
          <button onClick={onLogout} className="text-sm text-neutral-600 hover:underline">
            Log out
          </button>
        </div>
      </header>

      {melds === null && <p className="text-sm text-neutral-500">Loading…</p>}

      {melds !== null && melds.length === 0 && (
        <div className="rounded border border-dashed border-neutral-300 p-12 text-center">
          <p className="mb-4 text-neutral-600">You haven&apos;t started a Meld yet.</p>
          <button onClick={onNewMeld} className="rounded bg-neutral-900 px-4 py-2 text-sm text-white">
            Write your first story
          </button>
        </div>
      )}

      {melds !== null && melds.length > 0 && (
        <ul className="flex flex-col gap-3">
          {melds.map((meld) => (
            <MeldCard key={meld.id} meld={meld} onRename={onRename} onDelete={onDelete} />
          ))}
        </ul>
      )}
    </main>
  );
}
