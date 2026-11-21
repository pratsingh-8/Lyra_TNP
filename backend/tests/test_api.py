import uuid

import pytest


def sid() -> str:
    return "s-" + uuid.uuid4().hex[:8]


def uniq(text="hello") -> str:
    return f"{text} {uuid.uuid4().hex}"


def chat(client, message, session_id=None, headers=None, **extra):
    body = {"session_id": session_id or sid(), "message": message, **extra}
    return client.post("/chat", json=body, headers=headers or {})


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "keys_configured": 2, "cache_enabled": True}


def test_chat_contract_fields(client):
    r = chat(client, uniq())
    assert r.status_code == 200
    body = r.json()
    meta = body["metadata"]
    assert body["reply"]
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    assert meta["model"] == "gemini/gemini-2.0-flash"
    assert isinstance(meta["latency_ms"], int)
    assert meta["input_tokens"] > 0 and meta["output_tokens"] > 0
    assert meta["key_id"] == "key_1"
    assert meta["fallback_attempts"] == 0


def test_cache_hit_in_new_session_and_different_message_misses(client):
    msg = uniq("capital of France")
    first = chat(client, msg).json()
    second = chat(client, msg).json()  # new session, same first message
    assert first["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert second["metadata"]["resolution_source"] == "LLM_CACHE"
    assert second["reply"] == first["reply"]
    assert second["metadata"]["key_id"] is None
    assert second["metadata"]["fallback_attempts"] == 0
    assert second["metadata"]["input_tokens"] == first["metadata"]["input_tokens"]
    assert second["metadata"]["output_tokens"] == first["metadata"]["output_tokens"]

    other = chat(client, uniq("something else")).json()
    assert other["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_tokens_are_per_request_not_cumulative(client):
    s = sid()
    a = chat(client, uniq("one"), s).json()["metadata"]["input_tokens"]
    b = chat(client, uniq("two"), s).json()["metadata"]["input_tokens"]
    c = chat(client, uniq("one"), sid()).json()["metadata"]["input_tokens"]
    assert b > a            # history grows the prompt...
    assert abs(c - a) <= 5  # ...but a fresh session of equal length is ~the same as the first


def test_multi_turn_history_and_session_isolation(client):
    s1, s2 = sid(), sid()
    chat(client, uniq("hi"), s1)
    chat(client, uniq("again"), s1)
    chat(client, uniq("other"), s2)
    h1 = client.get(f"/sessions/{s1}/messages").json()
    h2 = client.get(f"/sessions/{s2}/messages").json()
    assert [m["role"] for m in h1["messages"]] == ["user", "assistant", "user", "assistant"]
    assert len(h2["messages"]) == 2
    assert all(m["role"] != "system" for m in h1["messages"])
    assert "metadata" not in h1["messages"][0]
    assert {"resolution_source", "input_tokens", "output_tokens"} <= set(h1["messages"][1]["metadata"])


def test_unknown_session_404(client):
    r = client.get("/sessions/nope-" + uuid.uuid4().hex + "/messages")
    assert r.status_code == 404
    assert r.json() == {"detail": "session not found"}


@pytest.mark.parametrize(
    "body",
    [
        {"session_id": "s", "message": ""},
        {"session_id": "s", "message": "   "},
        {"session_id": "", "message": "hi"},
        {"message": "hi"},
        {"session_id": "s"},
        {"session_id": "s", "message": "x" * 4001},
        {"session_id": "s" * 129, "message": "hi"},
    ],
)
def test_validation_422(client, body):
    r = client.post("/chat", json=body)
    assert r.status_code == 422
    assert "detail" in r.json()


def test_malformed_json_422(client):
    r = client.post("/chat", content="{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    assert "detail" in r.json()


def test_failover_via_header(client):
    r = chat(client, uniq(), headers={"X-Simulate-Failure": "key_1=429"})
    assert r.status_code == 200
    meta = r.json()["metadata"]
    assert meta["key_id"] == "key_2"
    assert meta["fallback_attempts"] == 1
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    r = chat(client, uniq(), headers={"X-Simulate-Failure": "key_1=500"})
    assert r.json()["metadata"]["key_id"] == "key_2"


def test_all_keys_fail_503_and_nothing_stored(client):
    s = sid()
    r = chat(client, uniq(), s, headers={"X-Simulate-Failure": "*=429"})
    assert r.status_code == 503
    assert r.json() == {"detail": "all providers unavailable"}
    assert client.get(f"/sessions/{s}/messages").status_code == 404


def test_real_provider_failure_falls_over(client, fake_provider):
    fake_provider["fail_keys"] = {"test-key-1"}
    try:
        r = chat(client, uniq())
    finally:
        fake_provider["fail_keys"] = set()
    assert r.status_code == 200
    assert r.json()["metadata"]["key_id"] == "key_2"


def test_system_prompt_is_stored_but_not_in_history(client):
    s = sid()
    r = chat(client, uniq(), s, system_prompt="You are a pirate.")
    assert r.status_code == 200
    msgs = client.get(f"/sessions/{s}/messages").json()["messages"]
    assert all("pirate" not in m["content"] for m in msgs)
