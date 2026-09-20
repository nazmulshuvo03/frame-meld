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
    // False positive: refresh() only calls setMelds/setLoadError after
    // `await listMelds()` resolves, so nothing here runs synchronously
    // within the effect body — there is no cascading-render risk to warn
    // about.
    // eslint-disable-next-line react-hooks/set-state-in-effect
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
    <>
      {/* The same shell the studio wears: sticky, one hairline rule, the
          content on the same 5xl column. This page used to be 2xl and
          unbordered, so crossing into a Meld re-laid the whole window out
          and the two screens read as two products. */}
      <header className="sticky top-0 z-20 border-b border-subtle bg-background/95 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center gap-x-6 gap-y-2 px-6 py-3">
          <span className="text-base font-medium" aria-hidden>
            FrameMeld
          </span>
          <h1 className="mr-auto text-base text-muted">My Melds</h1>
          <button
            onClick={onNewMeld}
            disabled={creating}
            className="rounded bg-accent px-3.5 py-1.5 text-sm text-accent-foreground disabled:opacity-50"
          >
            New Meld
          </button>
          <button onClick={onLogout} className="text-sm text-muted hover:text-foreground">
            Log out
          </button>
        </div>
      </header>

      <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-8">
        {loadError && (
          <p role="alert" className="mb-6 text-sm text-danger">
            {loadError}
          </p>
        )}

        {melds === null && !loadError && <p className="text-sm text-muted">Loading…</p>}

        {melds !== null && melds.length === 0 && (
          <div className="rounded-lg border border-dashed border-strong px-6 py-20 text-center">
            <p className="text-lg font-medium">You haven&apos;t started a Meld yet.</p>
            <p className="mx-auto mt-2 max-w-prose text-sm text-body">
              Write a story in your own words. Everything after it — the shot list, the cast, the
              stills and the film — is made from that.
            </p>
            <button
              onClick={onNewMeld}
              disabled={creating}
              className="mt-6 rounded bg-accent px-4 py-2 text-sm text-accent-foreground disabled:opacity-50"
            >
              Write your first story
            </button>
          </div>
        )}

        {melds !== null && melds.length > 0 && (
          <ul className="grid gap-4 sm:grid-cols-2">
            {melds.map((meld) => (
              <MeldCard key={meld.id} meld={meld} onRename={onRename} onDelete={onDelete} />
            ))}
          </ul>
        )}
      </main>
    </>
  );
}
