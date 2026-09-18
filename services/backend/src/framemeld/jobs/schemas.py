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
    #: 0-100 while a render runs, or null for a job kind that has no notion
    #: of progress. The UI shows a percentage only when this is not null, so
    #: the two cases stay distinguishable on screen.
    progress_pct: int | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
