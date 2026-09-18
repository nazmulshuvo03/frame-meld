import uuid

import httpx
import pytest
from sqlalchemy import select
from test_render_api import _user_id, drawn, scripted

from framemeld.assets.service import wav_duration
from framemeld.config import settings
from framemeld.credits.service import balance
from framemeld.models.asset import Asset
from framemeld.models.job import Job, JobState
from framemeld.models.render import Render
from framemeld.models.script import ArtifactState
from framemeld.models.shots import AudioClip, AudioOwnerType
from framemeld.providers.base import (
    ProviderRefusedError,
    ProviderResponseError,
    ProviderTransportError,
)
from framemeld.render.client import HttpRenderClient
from framemeld.storage.registry import get_storage
from framemeld.worker import run_generate_music, run_render_preview

#: A tiny but structurally real MP4 header. The worker stores whatever it is
#: handed, so the bytes only have to be distinguishable and non-empty.
FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


class _FakeRenderClient:
    def __init__(self, error=None, video: bytes = FAKE_MP4):
        self._error = error
        self._video = video
        self.specs: list[dict] = []

    async def render(self, spec):
        self.specs.append(spec)
        if self._error is not None:
            raise self._error
        return self._video


# --- music ----------------------------------------------------------------


async def test_music_is_recorded_stored_and_measured(logged_in, db):
    meld_id = await scripted(logged_in, db)
    started = await logged_in.post(f"/melds/{meld_id}/film/music")

    outcome = await run_generate_music(db, uuid.UUID(started.json()["id"]))
    assert outcome.state is JobState.SUCCEEDED

    clip = (
        await db.execute(select(AudioClip).where(AudioClip.owner_type == AudioOwnerType.MELD))
    ).scalar_one()
    assert clip.state is ArtifactState.FRESH
    assert clip.asset_id is not None
    # Copied off the asset, because M5's scene spec lays out a timeline from
    # this column and must not join to `assets` for it.
    assert clip.duration_sec is not None and clip.duration_sec > 0

    asset = (await db.execute(select(Asset).where(Asset.id == clip.asset_id))).scalar_one()
    data = await get_storage().get(key=asset.storage_key)
    assert data.startswith(b"RIFF")
    assert wav_duration(data) == clip.duration_sec


async def test_the_score_is_as_long_as_the_film(logged_in, db):
    meld_id = await scripted(logged_in, db)
    expected = (await logged_in.get(f"/melds/{meld_id}/film")).json()["duration_sec"]

    started = await logged_in.post(f"/melds/{meld_id}/film/music")
    await run_generate_music(db, uuid.UUID(started.json()["id"]))

    clip = (
        await db.execute(select(AudioClip).where(AudioClip.owner_type == AudioOwnerType.MELD))
    ).scalar_one()
    assert float(clip.duration_sec or 0) == pytest.approx(round(expected), abs=1)


async def test_a_refused_score_refunds_in_full(logged_in, db, monkeypatch):
    meld_id = await scripted(logged_in, db)
    before = await balance(db, await _user_id(db))

    class _Refusing:
        name, model = "fake", "fake"

        async def compose(self, *, prompt, duration_sec, params):
            raise ProviderRefusedError("No.")

    monkeypatch.setattr("framemeld.worker.get_music_provider", lambda: _Refusing())
    started = await logged_in.post(f"/melds/{meld_id}/film/music")
    outcome = await run_generate_music(db, uuid.UUID(started.json()["id"]))

    assert outcome.state is JobState.FAILED
    assert outcome.retryable is False
    assert await balance(db, await _user_id(db)) == before


# --- the preview render ---------------------------------------------------


async def test_a_preview_is_rendered_stored_and_described(logged_in, db, monkeypatch):
    meld_id = await drawn(logged_in, db)
    client = _FakeRenderClient()
    monkeypatch.setattr("framemeld.worker.get_render_client", lambda: client)

    started = await logged_in.post(f"/melds/{meld_id}/film/preview")
    outcome = await run_render_preview(db, uuid.UUID(started.json()["id"]))
    assert outcome.state is JobState.SUCCEEDED

    render = (await db.execute(select(Render).where(Render.meld_id == meld_id))).scalar_one()
    assert render.state is ArtifactState.FRESH
    assert render.asset_id is not None
    assert render.resolution == "1280x720"
    assert render.duration_sec is not None and render.duration_sec > 0
    assert render.credits_spent == 0

    asset = (await db.execute(select(Asset).where(Asset.id == render.asset_id))).scalar_one()
    assert asset.mime == "video/mp4"
    assert await get_storage().get(key=asset.storage_key) == FAKE_MP4


async def test_the_render_service_receives_signed_urls_it_can_fetch(logged_in, db, monkeypatch):
    """The point of M5-D1. The render service has no cookie, so an
    unsigned URL would 401 on every still and the film would be black."""
    meld_id = await drawn(logged_in, db)
    client = _FakeRenderClient()
    monkeypatch.setattr("framemeld.worker.get_render_client", lambda: client)

    started = await logged_in.post(f"/melds/{meld_id}/film/preview")
    await run_render_preview(db, uuid.UUID(started.json()["id"]))
    await db.commit()

    spec = client.specs[0]
    for shot in spec["shots"]:
        assert "sig=" in shot["still_url"] and "exp=" in shot["still_url"]
        # And it really opens the file, with no session at all.
        path = shot["still_url"].removeprefix(settings.public_api_url)
        assert (await logged_in.get(path)).status_code == 200


async def test_the_stored_spec_holds_no_signed_urls(logged_in, db, monkeypatch):
    """A stored signature is a stored credential that also expires."""
    meld_id = await drawn(logged_in, db)
    monkeypatch.setattr("framemeld.worker.get_render_client", lambda: _FakeRenderClient())

    started = await logged_in.post(f"/melds/{meld_id}/film/preview")
    await run_render_preview(db, uuid.UUID(started.json()["id"]))

    render = (await db.execute(select(Render).where(Render.meld_id == meld_id))).scalar_one()
    assert "sig=" not in str(render.scene_spec)
    assert "http" not in str(render.scene_spec)


async def test_the_music_reaches_the_film(logged_in, db, monkeypatch):
    meld_id = await drawn(logged_in, db)
    music = await logged_in.post(f"/melds/{meld_id}/film/music")
    await run_generate_music(db, uuid.UUID(music.json()["id"]))
    await db.commit()

    client = _FakeRenderClient()
    monkeypatch.setattr("framemeld.worker.get_render_client", lambda: client)
    started = await logged_in.post(f"/melds/{meld_id}/film/preview")
    await run_render_preview(db, uuid.UUID(started.json()["id"]))

    assert client.specs[0]["music_url"] is not None
    # Ducked under the narration, not level with it.
    assert client.specs[0]["music_gain_db"] < 0


async def test_a_film_edited_mid_render_is_refused_rather_than_mislabelled(
    logged_in, db, monkeypatch
):
    """The job carries the key it was queued under. Rendering an edited
    film under it would leave a render claiming to be something it is not."""
    meld_id = await drawn(logged_in, db)
    monkeypatch.setattr("framemeld.worker.get_render_client", lambda: _FakeRenderClient())
    started = await logged_in.post(f"/melds/{meld_id}/film/preview")

    shot = (await logged_in.get(f"/melds/{meld_id}/shots")).json()["shots"][0]
    await logged_in.patch(f"/melds/{meld_id}/shots/{shot['id']}", json={"duration_sec": "1.5"})
    await db.commit()

    outcome = await run_render_preview(db, uuid.UUID(started.json()["id"]))
    assert outcome.state is JobState.FAILED
    assert outcome.retryable is False
    job = (
        await db.execute(select(Job).where(Job.id == uuid.UUID(started.json()["id"])))
    ).scalar_one()
    assert "changed while it was rendering" in (job.error or "")


async def test_a_render_service_outage_is_retried_then_terminal(logged_in, db, monkeypatch):
    meld_id = await drawn(logged_in, db)
    monkeypatch.setattr(
        "framemeld.worker.get_render_client",
        lambda: _FakeRenderClient(ProviderTransportError("down")),
    )
    started = await logged_in.post(f"/melds/{meld_id}/film/preview")
    job_id = uuid.UUID(started.json()["id"])

    first = await run_render_preview(db, job_id)
    assert first.state is JobState.QUEUED
    assert first.retryable is True

    second = await run_render_preview(db, job_id)
    assert second.retryable is True
    third = await run_render_preview(db, job_id)
    assert third.state is JobState.FAILED
    assert third.retryable is False

    render = (await db.execute(select(Render).where(Render.meld_id == meld_id))).scalar_one()
    assert render.state is ArtifactState.FAILED


async def test_a_failed_render_costs_nothing_because_nothing_was_held(logged_in, db, monkeypatch):
    meld_id = await drawn(logged_in, db)
    before = await balance(db, await _user_id(db))
    monkeypatch.setattr(
        "framemeld.worker.get_render_client",
        lambda: _FakeRenderClient(ProviderResponseError("the render failed")),
    )
    started = await logged_in.post(f"/melds/{meld_id}/film/preview")
    await run_render_preview(db, uuid.UUID(started.json()["id"]))

    assert await balance(db, await _user_id(db)) == before


# --- the HTTP contract with the render service (spec section 3.1) ----------


def _client(handler) -> HttpRenderClient:
    return HttpRenderClient(transport=httpx.MockTransport(handler))


SPEC = {"version": 1, "shots": []}


async def test_submit_poll_download_is_one_call_from_outside():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.method == "POST":
            return httpx.Response(202, json={"render_id": "r1", "status": "queued"})
        if request.url.path == "/render/r1":
            return httpx.Response(
                200,
                json={
                    "status": "done",
                    "progress": 1,
                    "output_url": "http://render.test/render/r1/output",
                },
            )
        return httpx.Response(200, content=FAKE_MP4)

    assert await _client(handler).render(SPEC) == FAKE_MP4
    assert seen == ["POST /render", "GET /render/r1", "GET /render/r1/output"]


async def test_a_render_that_fails_is_terminal_and_says_why():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"render_id": "r1"})
        return httpx.Response(200, json={"status": "failed", "error": "a still would not load"})

    with pytest.raises(ProviderResponseError, match="a still would not load"):
        await _client(handler).render(SPEC)


async def test_a_service_that_is_down_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with pytest.raises(ProviderTransportError):
        await _client(handler).render(SPEC)


async def test_a_spec_the_service_refuses_is_not_retried():
    """4xx on submit means the document will never parse. Retrying it is
    three round trips to be told the same thing."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "version 2 is not supported"})

    with pytest.raises(ProviderResponseError):
        await _client(handler).render(SPEC)


async def test_a_render_that_never_finishes_times_out_retryably(monkeypatch):
    monkeypatch.setattr(settings, "render_timeout_seconds", 0)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"render_id": "r1"})
        return httpx.Response(200, json={"status": "rendering", "progress": 0.3})

    with pytest.raises(ProviderTransportError, match="too long"):
        await _client(handler).render(SPEC)


async def test_an_empty_download_is_refused_rather_than_stored():
    """An empty file would become a zero-byte asset that a browser shows as
    a broken video, with a job row saying it succeeded."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"render_id": "r1"})
        if request.url.path == "/render/r1":
            return httpx.Response(
                200, json={"status": "done", "output_url": "http://render.test/render/r1/output"}
            )
        return httpx.Response(200, content=b"")

    with pytest.raises(ProviderResponseError):
        await _client(handler).render(SPEC)
