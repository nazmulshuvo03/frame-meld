export type StageId = "story" | "script" | "cast" | "shots" | "film";

/** The five pages of a Meld, in the order the work happens. One list, read
 *  by the sidebar's pipeline, the Back/Next footer and each page's "Step n of
 *  05", so the three cannot disagree about what comes next. */
export const STAGES: { id: StageId; label: string }[] = [
  { id: "story", label: "Story" },
  { id: "script", label: "Script" },
  { id: "cast", label: "Cast" },
  { id: "shots", label: "Shots" },
  { id: "film", label: "Film" },
];

export function stageIndex(id: StageId): number {
  return STAGES.findIndex((stage) => stage.id === id);
}

/** A stage's position as the prototype numbers it: `01` to `05`. */
export function stageNumber(id: StageId): string {
  return String(stageIndex(id) + 1).padStart(2, "0");
}

/** The stage a pathname is on. Anything unrecognised reads as the first,
 *  which is also where a bare `/meld/:id` is sent. */
export function stageFromPath(pathname: string | null): StageId {
  const last = (pathname ?? "").split("/").filter(Boolean).pop();
  return STAGES.find((stage) => stage.id === last)?.id ?? "story";
}

export function stageHref(meldId: string, id: StageId): string {
  return `/meld/${meldId}/${id}`;
}

/** Seconds as `1:07`-style timecode. A film's length is read in minutes and
 *  seconds, not as `67.5s`. */
export function timecode(seconds: number): string {
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}
