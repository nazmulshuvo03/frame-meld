import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    state: str
    attempts: int
    #: Shown to the user verbatim when a generation fails, so whatever
    #: writes it must keep it readable.
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
