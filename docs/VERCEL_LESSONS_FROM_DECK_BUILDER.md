# Lessons from Ppttx maker (easy Vercel deploy)

The **Admind Deck Builder** (`Ppttx maker`) deploys to Vercel with almost no friction. **Private-AI** is harder because it puts the whole FastAPI gateway (including document ingest) on Vercel. This note compares the two and gives a recommended path.

## Side-by-side

| | **Ppttx maker** | **Private-AI (today)** |
| --- | --- | --- |
| **What Vercel runs** | Next.js only (`web/`) | FastAPI Python (`app/` via `vercel.json` Services) |
| **Vercel root directory** | `web` | `.` (repo root) |
| **Framework detection** | Auto Next.js, no custom `vercel.json` | Multi-service / FastAPI; extra tiles (`benchmarks`, etc.) |
| **File uploads** | Browser → **Cloudflare tunnel → engine** (bypasses Vercel) | Browser → **Vercel** → `POST /api/ingest` (4.5 MB Hobby limit) |
| **Heavy Python** | Docker on GPU PC (`engine/` + LibreOffice + ingest) | PyMuPDF / docx on **serverless** (cold starts, bundle size) |
| **LLM** | vLLM only on PC; engine calls `localhost:8080` | Vercel function calls `https://private-llm.../v1` |
| **Auth to API** | HMAC token from `GET /api/token` + optional `APP_PASSWORD` | No split; same origin or API key to vLLM |
| **Compose / infra** | `infra/docker-compose.yml` (not repo root) | `deploy/docker-compose.yml` (moved out of root) |
| **Public API URL** | `slides-api.admindagency.com` → `:8090` | `private-llm.admindagency.com` → vLLM `:8080` only (no gateway tunnel) |

## Why deck builder “just works” on Vercel

1. **Thin edge** — Vercel serves HTML/JS and mints tokens; no GPU, no parsers, no large POST bodies.
2. **Explicit split** — Documented in [`docs/13-deploy-for-team.md`](../../Ppttx%20maker/docs/13-deploy-for-team.md): UI on Vercel, engine on the office PC.
3. **Direct uploads** — From [`docs/02-architecture.md`](../../Ppttx%20maker/docs/02-architecture.md): files never transit Vercel (privacy + no body-size ceiling on the edge).
4. **Clean import** — Root Directory `web` avoids monorepo scanners seeing Python, Docker, benchmarks.

## What Private-AI can adopt (recommended production layout)

Mirror the deck pattern: **tunnel the gateway**, not only vLLM.

```text
https://chat.admindagency.com          -> Vercel (UI only, optional) OR tunnel -> :3000
https://private-ai-api.admindagency.com -> Cloudflare -> uvicorn FastAPI :3000 (ingest + /api/chat)
https://private-llm.admindagency.com/v1  -> Cloudflare -> vLLM :8080 (unchanged, no public browser access ideal)
```

| Piece | Where | URL |
| --- | --- | --- |
| Chat UI | Vercel static/Next **or** served by FastAPI on PC | `chat.*` |
| Gateway (`app/`) | GPU PC (`uvicorn` or Docker) | `private-ai-api.*` → `:3000` |
| vLLM | Docker `:8080` | internal / existing tunnel |
| Ingest + LibreOffice | Same PC as gateway | not on Vercel |

### Option A — Easiest ops (one hostname)

Run **only** FastAPI on the PC; expose via tunnel (no Vercel):

```powershell
cd app
uvicorn main:app --host 127.0.0.1 --port 3000
# Cloudflare: private-ai-api.admindagency.com -> http://localhost:3000
```

Users open `https://private-ai-api.admindagency.com` — same as local, no split CORS.

### Option B — Deck-style (Vercel UI + tunneled API)

1. **Vercel** — New project, root could be a future `web/` (Next.js) or static UI with `meta name="api-base"`.
2. **PC** — Gateway on `:3000`, env `OPENAI_BASE_URL=https://private-llm.admindagency.com/v1`.
3. **Tunnel** — `private-ai-api` → `localhost:3000`.
4. **Browser** — `fetch(`${API_BASE}/api/ingest`)` with `API_BASE=https://private-ai-api...` (CORS is already open on the gateway).

Optional later: copy deck builder’s **HMAC token** (`ENGINE_TOKEN_SECRET`) so the tunneled API is not world-writable.

### Option C — Keep current (all-in on Vercel)

Still valid for demos: root `vercel.json`, env `OPENAI_BASE_URL`, accept Hobby **4.5 MB** ingest limit and Python cold starts. See [`DEPLOY.md`](../DEPLOY.md).

## UI `API_BASE` hook

`app/static/js/app.js` reads:

```html
<meta name="api-base" content="https://private-ai-api.admindagency.com" />
```

in `index.html` (empty = same origin). Use this when the HTML is on Vercel and APIs are on the tunnel.

## Checklist to align with deck builder

- [ ] Cloudflare hostname for **gateway** `:3000` (separate from vLLM).
- [ ] `OPENAI_BASE_URL` on gateway points at `private-llm.../v1` (not `127.0.0.1` on Vercel).
- [ ] Large document uploads hit **tunnel API**, not Vercel (Option B or A).
- [ ] `CORS_ORIGINS` tightened when going public (today `*`; deck uses explicit Vercel origin).
- [ ] (Optional) Vercel root `web/` + token route like [`web/app/api/token/route.ts`](../../Ppttx%20maker/web/app/api/token/route.ts).

## References in Ppttx maker repo

| Doc | Topic |
| --- | --- |
| `docs/13-deploy-for-team.md` | Step-by-step Vercel + tunnel |
| `docs/02-architecture.md` | Why browser talks to engine directly |
| `docs/09-deployment.md` | Ports, CORS, env tables |
| `web/app/api/token/route.ts` | HMAC token minting |