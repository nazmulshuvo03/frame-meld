"""SQLAlchemy models. Import every model module here so that
Base.metadata is populated for Alembic autogenerate and for the
test-schema fixture."""

from framemeld.models.asset import Asset
from framemeld.models.cast import (
    MAX_CHARACTERS,
    Character,
    CharacterImage,
    CharacterImageKind,
    CharacterRole,
)
from framemeld.models.credits import CreditKind, CreditLedger
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
    "MAX_CHARACTERS",
    "MAX_SHOTS",
    "MAX_SHOT_SECONDS",
    "ArtifactState",
    "Asset",
    "CameraAngle",
    "Character",
    "CharacterImage",
    "CharacterImageKind",
    "CharacterRole",
    "CreditKind",
    "CreditLedger",
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
