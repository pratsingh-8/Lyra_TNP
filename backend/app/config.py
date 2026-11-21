"""Configuration, read from environment variables only."""
import os
from dataclasses import dataclass, field


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _is_true(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    api_keys: list[str] = field(default_factory=list)
    model: str = "gemini/gemini-2.0-flash"
    database_url: str = "sqlite:////data/app.db"
    allowed_origins: list[str] = field(default_factory=list)
    enable_failure_simulation: bool = False
    default_system_prompt: str = "You are a helpful, concise assistant."
    cache_type: str = "local"  # "local" (in-memory) or "disk"
    cache_dir: str = "/data/litellm_cache"
    request_timeout: float = 30.0

    @property
    def db_path(self) -> str:
        """sqlite:////data/app.db -> /data/app.db ; sqlite:///x.db -> x.db"""
        url = self.database_url
        prefix = "sqlite:///"
        if not url.startswith(prefix):
            raise ValueError("DATABASE_URL must start with sqlite:///")
        return url[len(prefix):]


def load_settings() -> Settings:
    return Settings(
        api_keys=_split(os.getenv("LLM_API_KEYS", "")),
        model=os.getenv("LLM_MODEL", "gemini/gemini-2.0-flash"),
        database_url=os.getenv("DATABASE_URL", "sqlite:////data/app.db"),
        allowed_origins=_split(os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")),
        enable_failure_simulation=_is_true(os.getenv("ENABLE_FAILURE_SIMULATION")),
        default_system_prompt=os.getenv(
            "DEFAULT_SYSTEM_PROMPT", "You are a helpful, concise assistant."
        ),
        cache_type=os.getenv("CACHE_TYPE", "local").lower(),
        cache_dir=os.getenv("CACHE_DIR", "/data/litellm_cache"),
        request_timeout=float(os.getenv("LLM_TIMEOUT", "30")),
    )
