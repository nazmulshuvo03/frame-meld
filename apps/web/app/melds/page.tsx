"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { MeldCard } from "@/components/MeldCard";
import { BrandMark } from "@/components/studio/StudioHeader";
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
      {/* The same header the studio wears: the brand over the sidebar's
          column, then the page's name, then the account. Crossing into a
          Meld keeps the frame and only changes what is inside it. */}
      <header className="sticky top-0 z-20 grid h-14 grid-cols-[auto_minmax(0,1fr)_auto] items-center border-b border-subtle bg-background lg:grid-cols-[248px_minmax(0,1fr)_auto]">
        <span className="flex h-full items-center gap-2.5 border-r border-subtle px-5" aria-hidden>
          <BrandMark />
          <span className="font-heading text-[19px] font-semibold tracking-[0.01em]">FrameMeld</span>
        </span>
        <span className="px-6 font-heading text-xl font-semibold">My melds</span>
        <button onClick={onLogout} className="btn btn-ghost mr-5 text-muted">
          Log out
        </button>
      </header>

      <main className="w-full flex-1 px-5 py-10 lg:px-10">
        <div className="mb-7 flex flex-wrap items-end justify-between gap-5">
          <div>
            <h1 className="text-[42px]">My melds</h1>
            <p className="mt-1 text-muted">Each meld turns one story into one film.</p>
          </div>
          <button onClick={onNewMeld} disabled={creating} className="btn btn-primary blueprint">
            New meld
          </button>
        </div>

        {loadError && (
          <p role="alert" className="mb-6 text-sm text-danger">
            {loadError}
          </p>
        )}

        {melds === null && !loadError && <p className="text-sm text-muted">Loading…</p>}

        {melds !== null && (
          <ul className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,300px),1fr))] gap-7">
            {melds.map((meld) => (
              <MeldCard key={meld.id} meld={meld} onRename={onRename} onDelete={onDelete} />
            ))}
            <li>
              <button
                onClick={onNewMeld}
                disabled={creating}
                className="flex size-full min-h-40 flex-col items-center justify-center gap-2 border border-dashed border-subtle p-6 text-muted hover:border-accent hover:text-accent-ink disabled:opacity-45"
              >
                <svg
                  width="22"
                  height="22"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  aria-hidden
                >
                  <path d="M12 5v14M5 12h14" />
                </svg>
                <span className="font-heading text-[17px] font-semibold">
                  {melds.length === 0 ? "Write your first story" : "Start from a story"}
                </span>
                {melds.length === 0 && (
                  <span className="max-w-xs text-center text-sm text-body">
                    Everything after it — the shot list, the cast, the stills and the film — is
                    made from that.
                  </span>
                )}
              </button>
            </li>
          </ul>
        )}
      </main>
    </>
  );
}
