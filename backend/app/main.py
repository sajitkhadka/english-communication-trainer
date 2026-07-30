"""FastAPI entry point.

    uv run uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import init_db
from .routers import sessions, system, vocab

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("ect")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    log.info("db ready at %s", settings.db_path)
    log.info(
        "whisper=%s compute=%s device=%s (models cached in %s)",
        settings.whisper_model,
        settings.compute_type,
        settings.device,
        settings.model_cache_dir,
    )
    yield


app = FastAPI(
    title="English Communication Trainer",
    version="0.1.0",
    summary="Local backend: audio ingest, WhisperX + Silero analysis, vocabulary DB.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sessions.router)
app.include_router(vocab.router)
app.include_router(system.router)


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {
        "app": "English Communication Trainer",
        "docs": "/docs",
        "health": "/api/health",
    }
