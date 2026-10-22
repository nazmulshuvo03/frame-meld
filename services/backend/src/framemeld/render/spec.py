import uuid
from dataclasses import dataclass
from decimal import Decimal

from framemeld.assets.signing import signed_asset_url
from framemeld.models.render import RenderKind
from framemeld.models.script import CameraAngle, Shot

#: Spec section 3.2. Bump only for a change the render service cannot read
#: -- it is the Python-to-Node contract's version, not the composition's
#: (that is COMPOSITION_VERSION, and it lives with the hashes because it
#: invalidates artifacts rather than breaking a parser).
SPEC_VERSION = 1

FPS = 30

#: Preview is the cheap iteration gate (spec section 4.2), so it renders at
#: 720p (M5-D6); Final is the deliverable and renders at spec section 3.2's
#: 1920x1080 (M6-D6). `renders.resolution` records which was used, so the
#: two are distinguishable after the fact.
PREVIEW_SIZE = (1280, 720)
FINAL_SIZE = (1920, 1080)


def size_for(kind: RenderKind) -> tuple[int, int]:
    return FINAL_SIZE if kind is RenderKind.FINAL else PREVIEW_SIZE


#: How far under the narration the score sits (M5-D12). A fixed duck rather
#: than sidechain compression: the fancier version is a mixing feature and
#: this milestone's job is that the draft is watchable.
MUSIC_GAIN_DB = -18.0


@dataclass(frozen=True)
class SpecAudio:
    asset_id: uuid.UUID
    content_hash: str
    start_sec: float
    gain_db: float


@dataclass(frozen=True)
class SpecShot:
    shot_id: uuid.UUID
    duration_sec: float
    #: None only in a Final, for a shot whose clip was made from the script
    #: alone (the composition reads the clip and never the still).
    still_asset_id: uuid.UUID | None
    still_content_hash: str | None
    #: [x, y, zoom] at the start and at the end of the shot. Derived, never
    #: authored -- see ken_burns below.
    motion_from: tuple[float, float, float]
    motion_to: tuple[float, float, float]
    audio: list[SpecAudio]
    #: The animated clip, in a Final render. None in a Preview, and None in
    #: a Final is a bug the assembler refuses before it gets here (M6-D7).
    clip_asset_id: uuid.UUID | None = None
    clip_content_hash: str | None = None


@dataclass(frozen=True)
class SceneSpec:
    """One film, fully resolved, in the two forms it is needed in.

    Carries asset *ids*, not URLs. A signed URL expires, so a spec that
    stored one would be a stored credential that also rots -- and it is
    what makes the hash and the request two views of one walk (M5-D8)
    rather than two walks that must agree.
    """

    kind: RenderKind
    width: int
    height: int
    shots: list[SpecShot]
    music: SpecAudio | None

    @property
    def duration_sec(self) -> Decimal:
        total = sum(shot.duration_sec for shot in self.shots)
        return Decimal(str(round(total, 2)))

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"

    def to_request(self) -> dict:
        """The document the render service receives (spec section 3.2).

        Every asset becomes a signed absolute URL here and nowhere else:
        the render service resolves nothing (spec section 3.1), so this is
        the last point at which an id can become something fetchable.
        """
        return {
            "version": SPEC_VERSION,
            "kind": self.kind.value,
            "fps": FPS,
            "width": self.width,
            "height": self.height,
            "music_url": None if self.music is None else signed_asset_url(self.music.asset_id),
            "music_gain_db": MUSIC_GAIN_DB if self.music else 0.0,
            "shots": [
                {
                    "id": str(shot.shot_id),
                    "duration_sec": shot.duration_sec,
                    "still_url": (
                        None
                        if shot.still_asset_id is None
                        else signed_asset_url(shot.still_asset_id)
                    ),
                    # Filled only in a Final. The composition reads it
                    # instead of the still, and ignores `motion` when it is
                    # there (spec section 4.2) -- which is the whole
                    # difference between the two modes.
                    "clip_url": (
                        None if shot.clip_asset_id is None else signed_asset_url(shot.clip_asset_id)
                    ),
                    "motion": {
                        "kind": "kenburns",
                        "from": list(shot.motion_from),
                        "to": list(shot.motion_to),
                    },
                    # Only cuts in v1. The field exists because the spec's
                    # contract has it and a renderer that learns dissolves
                    # should not need a spec version bump to be told.
                    "transition_in": {"kind": "cut"},
                    "audio": [
                        {
                            "url": signed_asset_url(clip.asset_id),
                            "start_sec": clip.start_sec,
                            "gain_db": clip.gain_db,
                        }
                        for clip in shot.audio
                    ],
                }
                for shot in self.shots
            ],
        }

    def to_hash_payload(self) -> dict:
        """The same document with content hashes where the URLs go (M5-D8).

        Deliberately the same shape as to_request, field for field, so the
        two cannot drift into describing different films. Asset ids are
        absent on purpose: an id changes on every regeneration even when
        the bytes do not, and the content hash is what spec section 4.1
        says downstream keys on.
        """
        return {
            "version": SPEC_VERSION,
            "kind": self.kind.value,
            "fps": FPS,
            "width": self.width,
            "height": self.height,
            "music": None if self.music is None else self.music.content_hash,
            "music_gain_db": MUSIC_GAIN_DB if self.music else 0.0,
            "shots": [
                {
                    "duration_sec": shot.duration_sec,
                    "still": shot.still_content_hash,
                    "clip": shot.clip_content_hash,
                    "motion": {"from": list(shot.motion_from), "to": list(shot.motion_to)},
                    "transition_in": "cut",
                    "audio": [
                        {
                            "clip": clip.content_hash,
                            "start_sec": clip.start_sec,
                            "gain_db": clip.gain_db,
                        }
                        for clip in shot.audio
                    ],
                }
                for shot in self.shots
            ],
        }


#: Per camera angle: (zoom at the start, zoom at the end, how far it drifts
#: across the frame). A still that simply sat there would read as a
#: slideshow rather than a film, and these are the numbers that make a
#: 5-second frame feel deliberate without looking like a zoom effect.
_MOTION: dict[CameraAngle, tuple[float, float, float]] = {
    # Establishing: a slow push into a wide frame, no lateral drift, so the
    # audience reads the geography before anything moves.
    CameraAngle.ESTABLISHING: (1.00, 1.08, 0.00),
    CameraAngle.WIDE: (1.02, 1.09, 0.04),
    CameraAngle.MEDIUM: (1.03, 1.10, 0.03),
    CameraAngle.CLOSE: (1.05, 1.13, 0.02),
    # Insert: a hand, a note, an object. Nearly still, because the point of
    # the shot is that the audience reads it.
    CameraAngle.INSERT: (1.00, 1.04, 0.01),
}


def ken_burns(shot: Shot, film_index: int) -> tuple[tuple[float, float, float], ...]:
    """Where the frame starts and ends, derived from the shot (M5-D5).

    `motion_prompt` is deliberately not read. It is prose written for an
    image-to-video model (spec section 8), and turning it into a pan would
    be guessing -- which is why it is also absent from the render's input
    hash: a field the renderer never reads must not invalidate a render.

    The drift alternates with position in the film so two consecutive shots
    never pan the same way. Without that, a cut between two shots moving
    left reads as one continuous camera and the edit disappears.
    """
    start_zoom, end_zoom, drift = _MOTION[shot.camera]
    direction = 1.0 if film_index % 2 == 0 else -1.0
    return (
        (round(-drift * direction, 4), 0.0, start_zoom),
        (round(drift * direction, 4), round(drift / 2, 4), end_zoom),
    )
