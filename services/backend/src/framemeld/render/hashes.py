from framemeld.config import settings
from framemeld.hashing import canonical_hash
from framemeld.models.meld import Meld
from framemeld.models.render import RenderKind
from framemeld.models.script import Scene

#: Bump when the music prompt template changes in a way that would score
#: the same film differently. Marks every existing track stale, which is
#: true and costs a regeneration, so bump it deliberately.
MUSIC_PROMPT_VERSION = 1

#: Bump when the Remotion composition changes in a way that would produce a
#: visibly different film from the same scene spec -- a different Ken Burns
#: curve, a different mix, a transition. Every existing render correctly
#: goes stale.
COMPOSITION_VERSION = 1

MUSIC_PARAMS: dict[str, object] = {
    "prompt_version": MUSIC_PROMPT_VERSION,
}


def music_prompt(meld: Meld, scenes: list[Scene]) -> str:
    """What to score this film like, derived rather than authored (M5-D3).

    A `music_prompt` column would be one more field to fill in before a
    user can hear anything, on the milestone whose whole point is that the
    first watchable draft is one click away. The Meld's style preset and
    its scene headings are already the closest thing this data model has to
    a statement of tone.
    """
    beats = ", ".join(scene.heading.strip() for scene in scenes if scene.heading.strip())
    style = (meld.style_preset or "cinematic").strip()
    return (
        f"Instrumental score for a short {style} film titled “{meld.title}”. "
        f"No vocals, no lyrics. It plays under narration, so it stays out of the way: "
        f"restrained, even in dynamics, with no abrupt hits."
        + (f" The film moves through: {beats}." if beats else "")
    )


def music_hash_for_meld(prompt: str, duration_sec: float) -> str:
    """The part of a Meld its score is actually made from.

    A narrow projection like every other one since M4-D2: the prompt text
    and the length it has to fill. Notably not the stills, the cast or the
    spoken lines -- redrawing a face must not re-score the film.

    The duration is rounded to the second. Re-recording one line changes
    the film's length by milliseconds, and a score that had to be bought
    again for that would be M3's sheet bug wearing a different hat.
    """
    return canonical_hash(
        {
            "kind": "meld_music",
            "prompt": prompt,
            "duration_sec": round(duration_sec),
        }
    )


def music_clip_input_hash(meld_music_hash: str) -> str:
    """What would produce this track right now.

    Reads the *music* provider settings, so switching image or speech
    models never re-scores anything. Fourth independent invalidation
    domain, same rule as the three before it.
    """
    return canonical_hash(
        {
            "kind": "music_clip",
            "meld_music_hash": meld_music_hash,
            "provider": settings.music_provider,
            "model": settings.music_model,
            "params": MUSIC_PARAMS,
        }
    )


def render_input_hash(spec_payload: dict, kind: RenderKind) -> str:
    """What would produce this film right now (M5-D8).

    `spec_payload` is the scene spec with each artifact's `content_hash`
    where its signed URL goes. Hashing the document as *sent* would put an
    expiry timestamp inside the key, so every render would be unique, the
    cache would never hit and pressing the button twice would re-render a
    film that had not changed. That is M4's self-invalidating casting hash
    arrived at from the other direction, and it is worth naming twice.

    The composition version is in here rather than in the payload because
    it is a property of the renderer, not of the film: changing the Ken
    Burns curve must invalidate every render, and must not have to be
    threaded through the assembler to do it.
    """
    return canonical_hash(
        {
            "kind": "render",
            "render_kind": kind.value,
            "spec": spec_payload,
            "composition_version": COMPOSITION_VERSION,
        }
    )


def content_hash_for_render(asset_sha256: str) -> str:
    """What the film *is*: a hash of the bytes that came back."""
    return canonical_hash({"kind": "render", "asset_sha256": asset_sha256})
