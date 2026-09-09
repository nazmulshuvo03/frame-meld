"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { MeldCard } from "@/components/MeldCard";
import {
  ApiError,
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
  const [loadError, setLoadError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setMelds(await listMelds());
      setLoadError(null);
    } catch (err) {
      // Only a 401 means the session is gone. Any other failure — the API
      // unreachable, a 500 — must not masquerade as a logout, or a
      // transient blip bounces an authenticated user to a login page that
      // will also fail.
      if (err instanceof ApiError && err.status === 401) {
        router.replace("/login");
      } else {
        setLoadError("Couldn't load your Melds. Check your connection and reload.");
      }
    }
  }, [router]);

  /** Turn a mutation failure into the right user-visible outcome.
   *
   *  401 — the session is gone; go to login rather than blaming the action.
   *  404 — the row is gone (deleted here, or in another tab). Resync so the
   *        card disappears; claiming the action failed would be a false alarm.
   *  else — a genuine transient failure, so say so.
   */
  const handleMutationError = useCallback(
    async (err: unknown, message: string) => {
      if (err instanceof ApiError && err.status === 401) {
        router.replace("/login");
        return;
      }
      if (err instanceof ApiError && err.status === 404) {
        await refresh();
        return;
      }
      alert(message);
    },
    [router, refresh],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function onNewMeld() {
    if (creating) return;
    setCreating(true);
    try {
      const created = await createMeld();
      router.push(`/meld/${created.id}`);
    } catch (err) {
      setCreating(false);
      await handleMutationError(err, "Couldn't create a Meld. Please try again.");
    }
  }

  async function onRename(id: string, title: string) {
    try {
      await renameMeld(id, title);
      await refresh();
    } catch (err) {
      await handleMutationError(err, "Couldn't rename this Meld. Please try again.");
    }
  }

  async function onDelete(id: string) {
    if (!confirm("Delete this Meld? You can't undo this from the app.")) return;
    try {
      await deleteMeld(id);
      await refresh();
    } catch (err) {
      await handleMutationError(err, "Couldn't delete this Meld. Please try again.");
    }
  }

  async function onLogout() {
    try {
      await logout();
    } catch {
      alert("Couldn't log out. Please try again.");
      return;
    }
    router.replace("/login");
  }

  return (
    <main className="mx-auto max-w-2xl p-8">
      <header className="mb-8 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">My Melds</h1>
        <div className="flex items-center gap-4">
          <button
            onClick={onNewMeld}
            disabled={creating}
            className="rounded bg-neutral-900 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            New Meld
          </button>
          <button onClick={onLogout} className="text-sm text-neutral-600 hover:underline">
            Log out
          </button>
        </div>
      </header>

      {loadError && (
        <p role="alert" className="mb-6 text-sm text-red-600">
          {loadError}
        </p>
      )}

      {melds === null && !loadError && <p className="text-sm text-neutral-500">Loading…</p>}

      {melds !== null && melds.length === 0 && (
        <div className="rounded border border-dashed border-neutral-300 p-12 text-center">
          <p className="mb-4 text-neutral-600">You haven&apos;t started a Meld yet.</p>
          <button
            onClick={onNewMeld}
            disabled={creating}
            className="rounded bg-neutral-900 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
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
