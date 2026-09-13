from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from framemeld.auth.router import router as auth_router
from framemeld.config import settings
from framemeld.credits.router import router as credits_router
from framemeld.melds.router import router as melds_router
from framemeld.script.router import router as script_router

app = FastAPI(
    title="FrameMeld API",
    # Anonymous callers can otherwise enumerate every endpoint and schema.
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(credits_router)
app.include_router(melds_router)
# Same prefix as melds_router (/melds/{meld_id}), but path parameters do not
# match across "/" so the two never collide. Mounted after melds_router
# anyway, so /melds/{meld_id} keeps resolving to the melds detail route
# first if that ever stops being true.
app.include_router(script_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
