import React from "react";
import {
  AbsoluteFill,
  Audio,
  Img,
  OffthreadVideo,
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

/** One shot: its clip if it has one, otherwise its still panned and zoomed,
 *  with its own audio over it.
 *
 *  That one conditional is the entire difference between a Preview and a
 *  Final (§4.2), and it is why Preview was cheap to build: same composition,
 *  same spec, same mix, different visual source. A clip carries its own
 *  movement, so `motion` is ignored when one is present rather than applied
 *  on top of it.
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

  // cover, so a source whose aspect ratio does not match the frame fills it
  // rather than letterboxing inside a film the player already letterboxes.
  const fill = { width: "100%", height: "100%", objectFit: "cover" } as const;

  return (
    <AbsoluteFill style={{ backgroundColor: "black", overflow: "hidden" }}>
      {shot.clip_url ? (
        // OffthreadVideo rather than Video: it extracts frames with ffmpeg
        // instead of asking a headless browser to play a video in real time,
        // which is both faster and the only one that renders deterministically.
        //
        // muted, and that is a statement about the film rather than about a
        // provider. Spec §8 assumed image-to-video models return silent
        // clips and built the audio design on laying narration, dialogue and
        // music over that silence — but Veo 3.1 returns an AAC track and
        // refuses `generateAudio: false`, so an unmuted clip would play the
        // model's invented ambience on top of the narration. The mix comes
        // from `shot.audio` and the score, always, whatever the clip carries.
        <OffthreadVideo src={shot.clip_url} style={fill} muted />
      ) : (
        <Img
          src={shot.still_url ?? ""}
          style={{
            ...fill,
            transform: `scale(${zoom}) translate(${at(fromX, toX) * 100}%, ${
              at(fromY, toY) * 100
            }%)`,
          }}
        />
      )}
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
