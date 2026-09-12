import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MeldCreate(BaseModel):
    title: str = Field(default="Untitled Meld", min_length=1, max_length=200)


class MeldRename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class StorySave(BaseModel):
    # No min_length: clearing the editor is a valid save.
    text: str = Field(max_length=200_000)


class StoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    text: str
    content_hash: str
    updated_at: datetime


class MeldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    stage: str
    status: str
    aspect_ratio: str
    created_at: datetime
    updated_at: datetime


class MeldDetail(MeldOut):
    story: StoryOut | None
