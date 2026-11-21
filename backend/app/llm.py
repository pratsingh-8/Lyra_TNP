"""LiteLLM gateway: key pool, automatic fallback, built-in response cache."""
import logging
from dataclasses import dataclass

import litellm
from litellm import Cache

from .config import Settings

log = logging.getLogger("llm")
litellm.suppress_debug_info = True

SIMULATED_STATUSES = {429, 500, 502, 503}


class AllProvidersUnavailable(Exception):
    """Every key in the pool failed."""


class SimulatedProviderError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"simulated provider failure: HTTP {status_code}")
        self.status_code = status_code


@dataclass
class LLMResult:
    reply: str
    resolution_source: str  # LIVE_INFERENCE | LLM_CACHE
    model: str
    input_tokens: int
    output_tokens: int
    key_id: str | None
    fallback_attempts: int


def parse_simulation_header(value: str | None) -> dict[str, int]:
    """'key_1=429,key_2=500' or '*=429' -> {'key_1': 429, ...}. Bad pairs are ignored."""
    result: dict[str, int] = {}
    if not value:
        return result
    for pair in value.split(","):
        if "=" not in pair:
            continue
        key, _, status = pair.partition("=")
        key, status = key.strip(), status.strip()
        if key and status.isdigit() and int(status) in SIMULATED_STATUSES:
            result[key] = int(status)
    return result


class LLMGateway:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = settings.model
        # Key ids are positional: key_1, key_2, ...
        self.keys = {f"key_{i}": k for i, k in enumerate(settings.api_keys, start=1)}
        self.cache_enabled = False
        self._setup_cache()

    def _setup_cache(self) -> None:
        try:
            if self.settings.cache_type == "disk":
                litellm.cache = Cache(type="disk", disk_cache_dir=self.settings.cache_dir)
            else:
                litellm.cache = Cache(type="local")
            self.cache_enabled = True
        except Exception:  # never let the cache stop the app from booting
            log.exception("could not enable LiteLLM cache")
            litellm.cache = None
            self.cache_enabled = False

    def complete(self, messages: list[dict], simulate: dict[str, int] | None = None) -> LLMResult:
        simulate = simulate or {}
        # When failures are simulated we skip the cache *read* so the failover path is
        # actually exercised (a cached answer would hide it). Results are still written.
        cache_ctl = {"no-cache": True} if simulate else {}
        failed = 0

        for key_id, api_key in self.keys.items():
            try:
                status = simulate.get(key_id, simulate.get("*"))
                if status:
                    raise SimulatedProviderError(status)

                kwargs = dict(
                    model=self.model,
                    messages=messages,
                    api_key=api_key,
                    timeout=self.settings.request_timeout,
                    num_retries=0,  # failover is ours: move on to the next key immediately
                )
                if self.cache_enabled:
                    kwargs["caching"] = True
                    if cache_ctl:
                        kwargs["cache"] = cache_ctl
                resp = litellm.completion(**kwargs)

                reply = (resp.choices[0].message.content or "").strip()
                if not reply:
                    raise ValueError("empty completion")

                hidden = getattr(resp, "_hidden_params", None) or {}
                cache_hit = bool(hidden.get("cache_hit"))
                usage = getattr(resp, "usage", None)
                return LLMResult(
                    reply=reply,
                    resolution_source="LLM_CACHE" if cache_hit else "LIVE_INFERENCE",
                    model=self.model,
                    input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                    output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                    key_id=None if cache_hit else key_id,
                    fallback_attempts=0 if cache_hit else failed,
                )
            except Exception as exc:  # 429, quota, 5xx, auth, timeout, empty reply...
                failed += 1
                log.warning("%s failed (%s: %s); trying next key", key_id, type(exc).__name__, exc)

        raise AllProvidersUnavailable()
