# MyAIBuddy

A production-shaped AI workspace: conversational assistant, RAG assistant grounded in
your documents, web research agent with citations, and an all-rounder agent.

## Quick start (Docker — recommended)

```bash
# 1. Configure once (creates .env from .env.example)
cp .env.example .env
#    Edit .env: set JWT_SECRET_KEY (production) + your AI provider keys

# 2. Start everything (DB + backend + frontend) — auto-runs migrations
bash setup.sh
#    or
make setup

# 3. Open the URLs printed on startup:
#    Frontend  http://localhost:8080
#    Backend   http://localhost:8000
#    Swagger   http://localhost:8000/docs
```

### Useful commands

```bash
make up          # start services
make status      # show status + listening ports
make logs        # tail all logs
make urls        # print all URLs
make stop        # stop everything
make restart     # restart services
make db-shell    # open PostgreSQL
make db-migrate  # run migrations
make db-reset    # wipe database (DESTRUCTIVE)
```

### All configuration lives in one file: `.env`

The root `.env` controls every service — ports, database, auth, CORS, AI provider keys,
RAG settings, rate limits. See `.env.example` for every option and its documentation.

## Deploying frontend & backend separately

Everything is containerized with named build targets, so you can deploy each part
independently on any platform (AWS, Render, Fly.io, a VPS, …).

```bash
# Standalone backend image (FastAPI :8000)
docker build --target backend -t myaibuddy-backend .
docker run -p 8000:8000 --env-file .env myaibuddy-backend

# Standalone frontend image (Nginx :80) — bake in the backend URL
docker build --target frontend \
  --build-arg VITE_API_BASE_URL=https://api.example.com \
  -t myaibuddy-frontend .
docker run -p 8080:80 myaibuddy-frontend
```

- `VITE_API_BASE_URL` is baked into the JS bundle so the SPA calls your external backend
  directly (CORS must include the frontend origin). Leave it empty when using
  docker-compose — Nginx proxies `/api`, `/docs`, `/openapi.json` to the backend.
- Render: `render.yml` ships ready-to-go (backend + frontend + managed Postgres).
- GitHub: `.github/workflows/` is ready for CI/CD builds.

## URLs

| Service | URL (docker) | URL (local dev) | Notes |
|---|---|---|---|
| Frontend (web dashboard) | http://localhost:8080 | http://127.0.0.1:5173 | React + Vite SPA (Nginx in Docker) |
| Backend API | http://localhost:8000 | http://127.0.0.1:8000 | FastAPI on port 8000 |
| Swagger UI | http://localhost:8000/docs | http://127.0.0.1:8000/docs | Register/login, then Authorize with the access token |
| ReDoc | http://localhost:8000/redoc | http://127.0.0.1:8000/redoc | Read-only API reference |
| OpenAPI JSON | http://localhost:8000/openapi.json | http://127.0.0.1:8000/openapi.json | Machine-readable spec |
| Health check | http://localhost:8000/api/v1/health | http://127.0.0.1:8000/api/v1/health | Returns 200 when the API is up |
| Readiness | http://localhost:8000/api/v1/ready | http://127.0.0.1:8000/api/v1/ready | Returns 200 when DB + app are ready |

> The ports above come from `.env` (`FRONTEND_PORT`, `BACKEND_PORT`, `DB_PORT`) and can
> be changed there. Docker-compose prints the live URLs on startup.

## Local development (without Docker — legacy flow)

```bash
# Backend (API + AI service)
(cd apps/backend && pnpm dev)   # or: uvicorn app.main:app --host 0.0.0.0 --port 8000

# Frontend (web dashboard)
(cd apps/frontend && pnpm dev)  # served on http://127.0.0.1:5173, proxies /api -> :8000
```

## API endpoints (`/api/v1`)

### Auth (`/auth`)
| Method | Path | Description |
|---|---|---|
| POST | `/auth/register` | Create a user. In dev, an optional `roles: ["admin"]` field grants the admin role. |
| POST | `/auth/login` | Login, returns `access_token` + `refresh_token` |
| POST | `/auth/refresh` | Exchange a refresh token for a new access token |
| GET | `/auth/me` | Current user profile |
| POST | `/auth/change-password` | Change the current user's password |

### Providers (`/providers`)
| Method | Path | Description |
|---|---|---|
| GET | `/providers` | List all providers with status, capabilities, default/order |
| PUT | `/providers/{provider}` | Configure/enable/disable/set default (admin only) |
| POST | `/providers/{provider}/test` | Ping a provider with a key (admin only) |

### 3rd-party APIs (`/integrations`)
| Method | Path | Description |
|---|---|---|
| GET | `/integrations` | List configured 3rd-party API integrations |
| POST | `/integrations` | Add a new API (endpoint, auth type, key, headers) — admin only |
| PUT | `/integrations/{id}` | Update an API integration (admin only) |
| DELETE | `/integrations/{id}` | Delete an API integration (admin only) |

### Conversations (`/conversations`)
| Method | Path | Description |
|---|---|---|
| GET | `/conversations` | List conversations (paginated) |
| POST | `/conversations` | Create a conversation |
| GET | `/conversations/{id}` | Conversation detail |
| DELETE | `/conversations/{id}` | Soft-delete a conversation |
| GET | `/conversations/{id}/messages` | List messages |
| POST | `/conversations/{id}/messages` | Send a message (JSON response) |
| POST | `/conversations/{id}/messages/stream` | Send a message (SSE stream) |

### Documents (`/documents`)
| Method | Path | Description |
|---|---|---|
| GET | `/documents?page=1&page_size=100` | List the shared knowledge base (each row carries `chunk_count` + `is_owner`) |
| POST | `/documents` | Upload one file (field `file`) |
| POST | `/documents/bulk` | Upload many files at once (field `files`, repeatable). Returns `{items, failed[]}` per file |
| DELETE | `/documents/{id}` | Delete a document — its uploader, or any admin |

Any signed-in user can upload. Chat sends the ids of every ready document as
`document_ids`; retrieval gives each attached file its best chunk (labelled by
filename) so one long file cannot crowd out the rest.

### Admin (`/admin`)
| Method | Path | Description |
|---|---|---|
| GET | `/admin/logs?min_level=WARNING&limit=100` | Recent in-memory backend logs (admin only) |

### Health
| Method | Path | Description |
|---|---|---|
| GET | `/health` | Liveness probe |
| GET | `/ready` | Readiness (checks DB) |

## Using Swagger (http://127.0.0.1:8000/docs)

1. **Register** an admin: `POST /api/v1/auth/register` with body
   ```json
   {"email":"dev@test.com","password":"TestPass123","display_name":"Dev","roles":["admin"]}
   ```
   (`roles` is honored only in development/test; it is ignored in production.)
2. **Login**: `POST /api/v1/auth/login` → copy `data.access_token`.
3. Click **Authorize** (top-right), paste the token, hit **Authorize**.
4. All protected endpoints (providers, conversations, auth/me, admin/logs) now work.
5. A pre-seeded admin `admin@myaibuddy.dev` also exists if you know its password.

## Provider priority (which model chat uses when you enable several)

Priority = **`fallback_order` ascending** — order `0` is the default (used first):

1. **Explicit request** — if a chat request names a provider, that one wins.
2. **Enabled providers in DB** — sorted by `fallback_order` asc (nulls last); the first
   one that is actually usable (has a key / base URL and supports chat) is picked.
   `mock` is always deferred to last resort.
3. **Env-configured providers** — `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`
   from `.env`, tried in that fixed order.
4. **Ollama** — if reachable (`OLLAMA_BASE_URL` + running server).
5. **mock** — absolute fallback, only if nothing above can answer.

How `fallback_order` is assigned in Settings:

- **Set default** → that provider gets `0`, every other ordered provider is bumped `+1`.
- **Enable** a provider that has no order yet → appended at the end (`max+1`).
- **Auto-promote** → enabling a chat-capable provider that can actually work makes it the
  default automatically when the current default is only `mock`.

Example — everything enabled:

| Provider | fallback_order | Used when |
|---|---|---|
| OpenAI | 0 (default) | always first if a key is set |
| Anthropic | 1 | if OpenAI unusable/unreachable |
| Ollama | 2 | next fallback |
| Google | 3 | next fallback |
| HuggingFace | any | **never for chat** (embedding-only, no chat support) |
| mock | any | last resort, only if all others unusable |

Unusable providers are skipped and logged (`provider_skipped`) with the reason — see
Settings → **System logs** (admin only) or `GET /api/v1/admin/logs`.

## Settings walkthrough

- **Manage AI providers** — enable/disable providers, set the default, configure keys and
  base URLs, and pick model names per capability (chat/reasoning/embedding/image).
  Each provider shows what it needs (e.g. "Requires OPENAI_API_KEY", "Local Ollama server"),
  which capabilities it supports, and suggested models for each field.
- **System logs** (admin only) — recent backend log records captured in-memory, filtered by
  level (DEBUG/INFO/WARNING/ERROR) with expandable tracebacks. Useful for diagnosing why a
  chat request fell back to `mock` or which provider failed.

## Documentation

- `docs/architecture.md` — components, data flow, module boundaries
- `docs/api-contracts.md` — endpoints, auth, request/response examples
- `docs/setup.md` — local setup
- `docs/deployment.md` — deploy without Docker
- `docs/security.md` — secrets, roles, data handling, approvals
- `docs/ai-design.md` — providers, model router, RAG, agents, tools, MCP, safety, evaluation

## Stack

- Frontend: React + TypeScript + Vite + Tailwind CSS + Zod + TanStack Query
- Backend: Python 3.12 + FastAPI + Pydantic v2 + SQLAlchemy 2 (async) + Alembic
- Database: PostgreSQL 16 + pgvector
- AI: LangChain providers + LangGraph agents (the all-rounder)

## Credentials & secrets

- API keys stored in `apps/backend/.env` are encrypted at rest (Fernet) and never returned
  to the browser (only a key fingerprint is exposed).
- Access tokens expire after 30 min; refresh tokens after 7 days.
- Admin-only endpoints are protected via a `Bearer` token with an `admin` role.