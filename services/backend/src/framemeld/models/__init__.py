"""SQLAlchemy models. Import every model module here so that
Base.metadata is populated for Alembic autogenerate and for the
test-schema fixture."""

from framemeld.models.job import Job, JobKind, JobState
from framemeld.models.meld import Meld, MeldStage, MeldStatus, ModerationState, Story
from framemeld.models.script import (
    MAX_SHOT_SECONDS,
    MAX_SHOTS,
    ArtifactState,
    CameraAngle,
    DialogueKind,
    Scene,
    Shot,
)
from framemeld.models.user import Session, User

__all__ = [
    "MAX_SHOTS",
    "MAX_SHOT_SECONDS",
    "ArtifactState",
    "CameraAngle",
    "DialogueKind",
    "Job",
    "JobKind",
    "JobState",
    "Meld",
    "MeldStage",
    "MeldStatus",
    "ModerationState",
    "Scene",
    "Session",
    "Shot",
    "Story",
    "User",
]
