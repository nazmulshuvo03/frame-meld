import uuid
from decimal import Decimal

from framemeld.models.render import RenderKind
from framemeld.models.script import CameraAngle, DialogueKind, Shot
from framemeld.render.hashes import (
    music_clip_input_hash,
    music_hash_for_meld,
    render_input_hash,
)
from framemeld.render.spec import (
    FPS,
    MUSIC_GAIN_DB,
    SPEC_VERSION,
    SceneSpec,
    SpecAudio,
    SpecShot,
    ken_burns,
)


def _shot(camera: CameraAngle = CameraAngle.MEDIUM, **kwargs) -> Shot:
    return Shot(
        id=uuid.uuid4(),
        scene_id=uuid.uuid4(),
        order_index=kwargs.get("order_index", 0),
        image_prompt=kwargs.get("image_prompt", "a room"),
        motion_prompt=kwargs.get("motion_prompt", "the camera pushes in"),
        duration_sec=Decimal(kwargs.get("duration_sec", "4.0")),
        camera=camera,
        dialogue_kind=DialogueKind.NONE,
        speech_text="",
        content_hash="c",
        input_hash="i",
    )


def _spec(shots: list[SpecShot], music: SpecAudio | None = None) -> SceneSpec:
    return SceneSpec(kind=RenderKind.PREVIEW, width=1280, height=720, shots=shots, music=music)


def _spec_shot(**kwargs) -> SpecShot:
    return SpecShot(
        shot_id=kwargs.get("shot_id", uuid.uuid4()),
        duration_sec=kwargs.get("duration_sec", 4.0),
        still_asset_id=kwargs.get("still_asset_id", uuid.uuid4()),
        still_content_hash=kwargs.get("still_content_hash", "still-hash"),
        motion_from=(0.0, 0.0, 1.0),
        motion_to=(0.03, 0.02, 1.1),
        audio=kwargs.get("audio", []),
    )


# --- motion (M5-D5) -------------------------------------------------------


def test_every_camera_angle_produces_some_movement():
    """A still that simply sits there reads as a slideshow, not a film."""
    for camera in CameraAngle:
        start, end = ken_burns(_shot(camera), 0)
        assert start != end


def test_consecutive_shots_drift_in_opposite_directions():
    """Two shots panning the same way read as one continuous camera and the
    cut between them disappears."""
    even_start, even_end = ken_burns(_shot(CameraAngle.WIDE), 0)
    odd_start, odd_end = ken_burns(_shot(CameraAngle.WIDE), 1)
    assert even_end[0] == -odd_end[0]
    assert even_start[0] == -odd_start[0]


def test_an_establishing_shot_pushes_in_without_panning():
    start, end = ken_burns(_shot(CameraAngle.ESTABLISHING), 0)
    assert start[0] == end[0] == 0.0
    assert end[2] > start[2]


def test_the_motion_prompt_does_not_change_the_motion():
    """It is prose for an image-to-video model (spec section 8). Parsing it
    into a pan would be guessing, which is why it is also absent from the
    render's hash."""
    plain = ken_burns(_shot(motion_prompt="the camera pushes in"), 0)
    other = ken_burns(_shot(motion_prompt="a violent handheld whip pan left"), 0)
    assert plain == other


# --- the two views of one spec (M5-D8) ------------------------------------


def test_the_request_carries_signed_urls_and_the_hash_payload_does_not():
    asset_id = uuid.uuid4()
    spec = _spec([_spec_shot(still_asset_id=asset_id)])

    request = spec.to_request()
    assert f"/assets/{asset_id}?" in request["shots"][0]["still_url"]
    assert "sig=" in request["shots"][0]["still_url"]

    payload = spec.to_hash_payload()
    assert str(asset_id) not in str(payload)
    assert payload["shots"][0]["still"] == "still-hash"


def test_the_hash_is_stable_across_two_assemblies_of_the_same_film():
    """The whole reason the payload exists. A signed URL carries an expiry,
    so hashing the document as sent would make every render unique and the
    cache would never hit -- M4's self-invalidating casting key, arrived at
    from the other direction."""
    spec = _spec([_spec_shot()])
    first = render_input_hash(spec.to_hash_payload(), RenderKind.PREVIEW)
    second = render_input_hash(spec.to_hash_payload(), RenderKind.PREVIEW)
    assert first == second
    # And the two requests genuinely differ, so the point is not vacuous.
    assert spec.to_request() != {**spec.to_request(), "shots": []}


def test_a_different_still_changes_the_hash():
    one = _spec([_spec_shot(still_content_hash="a")])
    two = _spec([_spec_shot(still_content_hash="b")])
    assert render_input_hash(one.to_hash_payload(), RenderKind.PREVIEW) != render_input_hash(
        two.to_hash_payload(), RenderKind.PREVIEW
    )


def test_a_new_asset_id_for_identical_bytes_does_not_change_the_hash():
    """Spec section 4.1's rule: downstream keys on content, not on which row
    happens to hold it."""
    one = _spec([_spec_shot(still_asset_id=uuid.uuid4(), still_content_hash="same")])
    two = _spec([_spec_shot(still_asset_id=uuid.uuid4(), still_content_hash="same")])
    assert render_input_hash(one.to_hash_payload(), RenderKind.PREVIEW) == render_input_hash(
        two.to_hash_payload(), RenderKind.PREVIEW
    )


def test_preview_and_final_of_the_same_film_are_different_renders():
    spec = _spec([_spec_shot()])
    assert render_input_hash(spec.to_hash_payload(), RenderKind.PREVIEW) != render_input_hash(
        spec.to_hash_payload(), RenderKind.FINAL
    )


def test_the_spec_reports_its_own_length_and_size():
    spec = _spec([_spec_shot(duration_sec=4.5), _spec_shot(duration_sec=3.25)])
    assert spec.duration_sec == Decimal("7.75")
    assert spec.resolution == "1280x720"


def test_the_contract_shape_matches_the_design_spec():
    """Spec section 3.2, field for field. This is the Python half of a
    hand-kept contract, so a rename here that the Node half does not hear
    about is exactly the failure worth pinning."""
    clip = SpecAudio(uuid.uuid4(), "clip-hash", 0.0, 0.0)
    spec = _spec([_spec_shot(audio=[clip])], music=SpecAudio(uuid.uuid4(), "music", 0.0, 0.0))
    request = spec.to_request()

    assert request["version"] == SPEC_VERSION
    assert request["kind"] == "preview"
    assert request["fps"] == FPS
    assert request["music_gain_db"] == MUSIC_GAIN_DB
    shot = request["shots"][0]
    assert set(shot) == {
        "id",
        "duration_sec",
        "still_url",
        "clip_url",
        "motion",
        "transition_in",
        "audio",
    }
    assert shot["clip_url"] is None
    assert shot["motion"]["kind"] == "kenburns"
    assert shot["transition_in"] == {"kind": "cut"}
    assert set(shot["audio"][0]) == {"url", "start_sec", "gain_db"}


def test_a_film_with_no_music_says_so_and_ducks_nothing():
    request = _spec([_spec_shot()]).to_request()
    assert request["music_url"] is None
    assert request["music_gain_db"] == 0.0


# --- the score's own hash (M5-D3) -----------------------------------------


def test_a_second_of_drift_does_not_re_score_the_film():
    """Re-recording one line moves the film's length by milliseconds. A
    score bought again for that would be M3's sheet bug in a new coat."""
    assert music_hash_for_meld("p", 90.2) == music_hash_for_meld("p", 90.4)


def test_a_real_change_of_length_does_re_score_it():
    assert music_hash_for_meld("p", 90.0) != music_hash_for_meld("p", 120.0)


def test_switching_music_model_invalidates_only_music(monkeypatch):
    from framemeld.config import settings

    before = music_clip_input_hash(music_hash_for_meld("p", 60))
    monkeypatch.setattr(settings, "music_model", "something-else")
    assert music_clip_input_hash(music_hash_for_meld("p", 60)) != before
