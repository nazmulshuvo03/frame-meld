import React from "react";
import { Composition } from "remotion";
import { type SceneSpec, totalFrames, validateSpec } from "../types.ts";
import { Film } from "./Film.tsx";

export const COMPOSITION_ID = "Film";

/** Enough of a spec to satisfy Remotion Studio and `selectComposition`
 *  before real input props arrive. Never rendered by the service — every
 *  real render passes its own spec — but a composition with no defaults
 *  cannot be opened in the Studio at all, which is where a change to the
 *  Ken Burns curve is actually judged. */
const PLACEHOLDER: SceneSpec = {
  version: 1,
  kind: "preview",
  fps: 30,
  width: 1280,
  height: 720,
  music_url: null,
  music_gain_db: 0,
  shots: [
    {
      id: "placeholder",
      duration_sec: 1,
      still_url: "",
      clip_url: null,
      motion: { kind: "kenburns", from: [0, 0, 1], to: [0, 0, 1] },
      transition_in: { kind: "cut" },
      audio: [],
    },
  ],
};

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id={COMPOSITION_ID}
      component={Film}
      defaultProps={{ spec: PLACEHOLDER }}
      // Every dimension of the film comes from the spec, so there is one
      // source of truth for size and length rather than a composition
      // whose declared duration has to be kept in step with its content.
      calculateMetadata={({ props }) => {
        const spec = validateSpec(props.spec);
        return {
          fps: spec.fps,
          width: spec.width,
          height: spec.height,
          durationInFrames: totalFrames(spec),
        };
      }}
      // Required by the type even though calculateMetadata replaces them.
      durationInFrames={30}
      fps={30}
      width={1280}
      height={720}
    />
  );
};
