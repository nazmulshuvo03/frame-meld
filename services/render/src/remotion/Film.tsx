import React from "react";
import {
  AbsoluteFill,
  Audio,
  Img,
  Sequence,
  Series,
  interpolate,
  useCurrentFrame,
} from "remotion";
import { type SceneSpec, type SpecShot, shotFrames } from "../types.ts";

/** Decibels to the linear multiplier Remotion's `volume` wants.
 *
 *  The spec expresses gain in dB because that is how a mix is described;
 *  the browser wants amplitude. One conversion, in one place, rather than
 *  hard-coded numbers that silently mean different things. */
function gain(db: number): number {
  return Math.pow(10, db / 20);
}

/** One shot: its still, panned and zoomed, with its own audio over it.
 *
 *  `clip_url` is deliberately not read. Preview animates the stills (§4.2);
 *  the clip branch arrives with M6, alongside the first clip that could
 *  exercise it.
 */
const Shot: React.FC<{ shot: SpecShot; fps: number }> = ({ shot, fps }) => {
  const frame = useCurrentFrame();
  const total = shotFrames(shot, fps);

  const [fromX, fromY, fromZoom] = shot.motion.from;
  const [toX, toY, toZoom] = shot.motion.to;
  // Linear, not eased. An eased pan accelerates and decelerates inside
  // every 5-second shot, which across thirty shots reads as the camera
  // breathing. A constant drift reads as a camera.
  const at = (from: number, to: number) =>
    interpolate(frame, [0, Math.max(1, total - 1)], [from, to], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    });

  const zoom = at(fromZoom, toZoom);

  return (
    <AbsoluteFill style={{ backgroundColor: "black", overflow: "hidden" }}>
      <Img
        src={shot.still_url}
        style={{
          width: "100%",
          height: "100%",
          // cover, so a still whose aspect ratio does not match the frame
          // fills it rather than letterboxing inside a film that is
          // already letterboxed by the player.
          objectFit: "cover",
          transform: `scale(${zoom}) translate(${at(fromX, toX) * 100}%, ${
            at(fromY, toY) * 100
          }%)`,
        }}
      />
      {shot.audio.map((clip, index) => (
        <Sequence
          key={`${shot.id}-${index}`}
          from={Math.round(clip.start_sec * fps)}
          // The audio is allowed to run to the end of the shot and no
          // further: a line longer than its shot is trimmed rather than
          // bleeding over the cut, which would desynchronise everything
          // after it.
          durationInFrames={Math.max(1, total - Math.round(clip.start_sec * fps))}
        >
          <Audio src={clip.url} volume={gain(clip.gain_db)} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};

export const Film: React.FC<{ spec: SceneSpec }> = ({ spec }) => {
  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      {spec.music_url ? (
        // One track across the whole film, ducked under the narration by a
        // fixed amount the spec carries (M5-D12). `loop` because a score
        // shorter than the film should keep playing rather than leave the
        // last minute silent.
        <Audio src={spec.music_url} volume={gain(spec.music_gain_db)} loop />
      ) : null}
      <Series>
        {spec.shots.map((shot) => (
          <Series.Sequence key={shot.id} durationInFrames={shotFrames(shot, spec.fps)}>
            <Shot shot={shot} fps={spec.fps} />
          </Series.Sequence>
        ))}
      </Series>
    </AbsoluteFill>
  );
};
