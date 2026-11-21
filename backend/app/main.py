"""FastAPI app: /health, /chat, /sessions/{id}/messages."""
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from .config import Settings, load_settings
from .db import Database
from .llm import AllProvidersUnavailable, LLMGateway, parse_simulation_header

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    system_prompt: Optional[str] = Field(default=None, max_length=8000)

    @field_validator("message", mode="before")
    @classmethod
    def _trim(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("system_prompt", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    db = Database(settings.db_path)
    gateway = LLMGateway(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        db.init()  # schema is created automatically on startup
        yield

    app = FastAPI(title="Resilient AI Chat", lifespan=lifespan)
    app.state.db, app.state.gateway, app.state.settings = db, gateway, settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    @app.get("/health")
    def health():
        db_ok = db.ping()
        keys = len(gateway.keys)
        body = {
            "status": "ok" if (db_ok and keys > 0) else "degraded",
            "keys_configured": keys,
            "cache_enabled": gateway.cache_enabled,
        }
        return JSONResponse(status_code=200 if body["status"] == "ok" else 503, content=body)

    @app.post("/chat")
    def chat(req: ChatRequest, x_simulate_failure: Optional[str] = Header(default=None)):
        started = time.perf_counter()

        simulate = (
            parse_simulation_header(x_simulate_failure)
            if settings.enable_failure_simulation
            else {}
        )

        # Per-session system prompt: request value overrides, else stored, else default.
        system_prompt = req.system_prompt or db.get_system_prompt(req.session_id) or settings.default_system_prompt
        history = db.get_history(req.session_id)
        messages = (
            [{"role": "system", "content": system_prompt}]
            + history
            + [{"role": "user", "content": req.message}]
        )

        try:
            result = gateway.complete(messages, simulate)
        except AllProvidersUnavailable:
            raise HTTPException(status_code=503, detail="all providers unavailable")

        latency_ms = max(1, int(round((time.perf_counter() - started) * 1000)))
        metadata = {
            "resolution_source": result.resolution_source,
            "model": result.model,
            "latency_ms": latency_ms,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "key_id": result.key_id,
            "fallback_attempts": result.fallback_attempts,
        }
        # Only successful exchanges are stored (422/503 never reach this line).
        db.save_exchange(req.session_id, req.system_prompt, req.message, result.reply, metadata)
        return {"session_id": req.session_id, "reply": result.reply, "metadata": metadata}

    @app.get("/sessions/{session_id}/messages")
    def get_messages(session_id: str):
        if not db.session_exists(session_id):
            raise HTTPException(status_code=404, detail="session not found")
        return {"session_id": session_id, "messages": db.get_messages(session_id)}

    return app


app = create_app()
