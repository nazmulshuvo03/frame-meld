from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from framemeld.config import settings

app = FastAPI(title="FrameMeld API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
