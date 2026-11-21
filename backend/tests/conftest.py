import os
import tempfile

# Configure the environment BEFORE the app module is imported.
_tmp = tempfile.mkdtemp()
os.environ["LLM_API_KEYS"] = "test-key-1,test-key-2"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["ENABLE_FAILURE_SIMULATION"] = "true"
os.environ["ALLOWED_ORIGINS"] = "http://localhost:3000"
os.environ["LLM_MODEL"] = "gemini/gemini-2.0-flash"

import litellm  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def provider_calls():
    """Records which api_key each *real* provider call used (cache hits never reach it)."""
    return []


@pytest.fixture(autouse=True)
def fake_provider(monkeypatch, provider_calls):
    """Wrap litellm.completion so it answers from `mock_response` (no network) while
    still going through LiteLLM's real cache layer."""
    real = litellm.completion
    state = {"fail_keys": set()}

    def fake(*args, **kwargs):
        key = kwargs.get("api_key")
        if key in state["fail_keys"]:
            raise litellm.RateLimitError("429 quota", llm_provider="gemini", model=kwargs["model"])
        provider_calls.append(key)
        last = kwargs["messages"][-1]["content"]
        kwargs["mock_response"] = f"echo: {last}"
        return real(*args, **kwargs)

    monkeypatch.setattr(litellm, "completion", fake)
    return state


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c
