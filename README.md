# Resilient AI Chat

A small full-stack AI chat app: React frontend, FastAPI backend, LiteLLM gateway (key pool, automatic failover, built-in cache) and SQLite session storage.

## 1. Live URLs

| Tier | URL |
|---|---|
| Frontend | `https://<your-frontend>.vercel.app` _(fill in after deploying)_ |
| Backend | `https://<your-backend>.onrender.com` _(fill in after deploying)_ |
| Backend health | `https://<your-backend>.onrender.com/health` |

## 2. Run locally

### With Docker (recommended)

```bash
cp .env.example .env      # then put 2+ real API keys in LLM_API_KEYS
make up                   # frontend http://localhost:3000, backend http://localhost:8000
make down
```

`make up` creates `.env` from `.env.example` if it is missing. SQLite lives in the named volume `appdata`, mounted at `/data`, so history survives `make down` / `make up`.

### Without Docker

```bash
# backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export LLM_API_KEYS=key-a,key-b DATABASE_URL=sqlite:///./app.db \
       ALLOWED_ORIGINS=http://localhost:3000 ENABLE_FAILURE_SIMULATION=true
uvicorn app.main:app --port 8000
python -m pytest -q       # tests (no network or real keys needed)

# frontend (new terminal)
cd frontend
npm install
VITE_API_URL=http://localhost:8000 npm run dev   # http://localhost:3000
```

### Deploying

- **Backend** (Render / Railway / Fly / HF Spaces): deploy `backend/` as a Docker service. Set `LLM_API_KEYS`, `ALLOWED_ORIGINS` (your frontend URL, no trailing slash), `ENABLE_FAILURE_SIMULATION=true`, `DATABASE_URL=sqlite:////data/app.db`, and attach a persistent disk at `/data` if you want history to survive redeploys. A `render.yaml` blueprint is included. The container honours `$PORT`.
- **Frontend** (Vercel / Netlify / Cloudflare Pages): root directory `frontend/`, build command `npm run build`, output `dist`, and set `VITE_API_URL` to the backend URL **before building** (Vite bakes it in).

### Environment variables

| Variable | Meaning |
|---|---|
| `LLM_API_KEYS` | Comma-separated key pool, at least 2. Ids are `key_1`, `key_2`, … in order |
| `LLM_MODEL` | LiteLLM model name (default `gemini/gemini-2.0-flash`) |
| `DATABASE_URL` | e.g. `sqlite:////data/app.db` |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins |
| `ENABLE_FAILURE_SIMULATION` | `true` enables `X-Simulate-Failure` |
| `DEFAULT_SYSTEM_PROMPT`, `CACHE_TYPE` (`local`/`disk`), `CACHE_DIR`, `LLM_TIMEOUT` | Optional |
| `VITE_API_URL` | Frontend build-time backend URL |

## 3. Design decisions

**Key fallback.** The gateway walks the key pool in order (`key_1`, `key_2`, …) and calls `litellm.completion(api_key=…)` with `num_retries=0`, so a failing key is abandoned immediately rather than retried. *Any* failure (429, quota, 5xx, timeout, auth error, empty completion) moves on to the next key. If every key fails the API returns `503 {"detail": "all providers unavailable"}`. The failed request is not stored. The response reports `key_id` (never the key) and `fallback_attempts`. Provider errors are logged, never leaked to the client, and a catch-all handler guarantees JSON errors with no stack traces.

**Caching.** LiteLLM's built-in cache (`litellm.cache = Cache(type="local")`, or `disk` via `CACHE_TYPE`). LiteLLM's cache key is the model plus the full message list (system prompt + history + new message), so the same first message in a new session is a hit and any different message is a miss. API keys are not part of the cache key, so a cached answer is served regardless of which key produced it. A hit is detected through LiteLLM's `cache_hit` flag and reported as `LLM_CACHE` with `key_id: null`, `fallback_attempts: 0`, and the stored token usage. Hits skip the network call, so they are much faster than live calls. When a failure is being simulated, the cache *read* is skipped so the failover path is actually exercised.

**SQLite session storage.** Tables `messages` (exact schema from the brief, plus an index on `(session_id, id)`) and `sessions` (stores the per-session system prompt), created on startup. The path comes from `DATABASE_URL` under the `/data` volume. Each request opens a short-lived connection, WAL mode is on, and a user+assistant pair is written in a single transaction only after a successful reply, so 422/503 never leave partial history. The client sends only the new message; the backend rebuilds the prompt from SQLite. Token counts are the provider's usage for that one request, never a running total.

**Other.** Pydantic validates inputs (trimmed message 1–4000 chars, session_id 1–128) and returns FastAPI's 422 shape. The Docker images are multi-stage (venv copy; nginx serving static assets), and the backend runs as a non-root user.

## 4. Verification

Replace `$API` with the backend URL.

```bash
# health
curl $API/health

# live call, then the same first message in a NEW session -> LLM_CACHE (faster, key_id null)
curl -s $API/chat -H 'Content-Type: application/json' -d '{"session_id":"a1","message":"What is the capital of France?"}'
curl -s $API/chat -H 'Content-Type: application/json' -d '{"session_id":"a2","message":"What is the capital of France?"}'

# failover: key_1 fails with 429, key_2 answers (key_id=key_2, fallback_attempts=1)
curl -s $API/chat -H 'Content-Type: application/json' -H 'X-Simulate-Failure: key_1=429' \
     -d '{"session_id":"a3","message":"Tell me a fact"}'

# every key fails -> 503 {"detail":"all providers unavailable"}, nothing stored
curl -si $API/chat -H 'Content-Type: application/json' -H 'X-Simulate-Failure: *=429' \
     -d '{"session_id":"a4","message":"hi"}'
curl -i $API/sessions/a4/messages        # 404 session not found

# multi-turn + history
curl -s $API/chat -H 'Content-Type: application/json' -d '{"session_id":"a5","message":"My name is Sam"}'
curl -s $API/chat -H 'Content-Type: application/json' -d '{"session_id":"a5","message":"What is my name?"}'
curl -s $API/sessions/a5/messages

# validation -> 422
curl -i $API/chat -H 'Content-Type: application/json' -d '{"session_id":"a6","message":""}'
```

**In the UI**, every assistant reply shows source, model, latency, and per-request input/output tokens. Open "Session settings" to set a system prompt or type `key_1=429` into the *Simulate failure* box to demo failover. Send the same first message after reloading the page (a new session) to see `LLM_CACHE`.

**Tests:** `make test` (or `cd backend && python -m pytest -q`) covers the contract, cache hits/misses, per-request tokens, multi-turn isolation, 404/422, header-simulated and real-error failover, and the all-keys-fail 503. They run offline using LiteLLM's `mock_response`, so the real cache layer is exercised without API keys.

## 5. Setup Used

_Fill in: editor (e.g. VS Code / Cursor) and AI tools (e.g. Claude) used to build and review this project._

## 6. Bonus features

- Optional per-session system prompt field in the UI, persisted per session in SQLite.
- "Simulate failure" box in the UI so failover can be demoed without curl.
- Optional disk cache (`CACHE_TYPE=disk`) so cache survives restarts.
- `key_id` and `fallback_attempts` shown in the UI; Docker healthcheck; Render blueprint.
