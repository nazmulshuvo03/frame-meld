/** The Python-to-Node contract (design spec §3.2).
 *
 *  This file is the Node half of it. The Python half is
 *  `services/backend/src/framemeld/render/spec.py`, and the two are kept in
 *  step by hand — there is no code generation, because the contract is one
 *  document with twelve fields and a generator would be more machinery than
 *  the thing it generates.
 *
 *  Everything here is already resolved. The render service reads no
 *  database, knows nothing about users, credits or auth (§3.1), and
 *  resolves no ids: every asset arrives as a signed URL it can simply GET.
 */

export type Motion = {
  kind: "kenburns";
  /** [x, y, zoom] at the start and at the end of the shot. x and y are
   *  fractions of the frame, so they are resolution-independent. */
  from: [number, number, number];
  to: [number, number, number];
};

export type SpecAudio = {
  url: string;
  start_sec: number;
  gain_db: number;
};

export type SpecShot = {
  id: string;
  duration_sec: number;
  still_url: string;
  /** Null in a preview. M6 fills it, and the composition will read it
   *  instead of the still (§4.2). */
  clip_url: string | null;
  motion: Motion;
  transition_in: { kind: string };
  audio: SpecAudio[];
};

export type SceneSpec = {
  version: number;
  kind: "preview" | "final";
  fps: number;
  width: number;
  height: number;
  music_url: string | null;
  music_gain_db: number;
  shots: SpecShot[];
};

/** The one spec version this service understands. A document that claims a
 *  version we do not know is rejected rather than rendered on a guess: the
 *  point of versioning the contract is that a mismatch is loud. */
export const SUPPORTED_SPEC_VERSION = 1;

export function validateSpec(value: unknown): SceneSpec {
  const spec = value as SceneSpec;
  if (!spec || typeof spec !== "object") throw new Error("The scene spec is not an object.");
  if (spec.version !== SUPPORTED_SPEC_VERSION) {
    throw new Error(
      `This service renders scene spec version ${SUPPORTED_SPEC_VERSION}, not ${spec.version}.`,
    );
  }
  if (!Array.isArray(spec.shots) || spec.shots.length === 0) {
    throw new Error("The scene spec has no shots.");
  }
  if (!spec.fps || !spec.width || !spec.height) {
    throw new Error("The scene spec is missing fps or a frame size.");
  }
  for (const shot of spec.shots) {
    if (!shot.still_url) throw new Error(`Shot ${shot.id} has no still.`);
    if (!(shot.duration_sec > 0)) throw new Error(`Shot ${shot.id} has no duration.`);
  }
  return spec;
}

/** Frames a shot occupies. At least one, so a duration that rounds to zero
 *  produces a frame rather than an empty sequence Remotion refuses. */
export function shotFrames(shot: SpecShot, fps: number): number {
  return Math.max(1, Math.round(shot.duration_sec * fps));
}

export function totalFrames(spec: SceneSpec): number {
  return spec.shots.reduce((sum, shot) => sum + shotFrames(shot, spec.fps), 0);
}
