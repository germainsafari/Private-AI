# Deploy checklist

## Vercel (this gateway)

**Use the existing project** (`private-ai`) or import the Git repo as a **single** Python app.

| Setting | Value |
| --- | --- |
| **Root Directory** | `.` (repository root) |
| **Framework** | Vercel reads root `vercel.json` → single **FastAPI** service `gateway` in `app/` |
| **Ignore extra tiles** | Do not import `benchmarks` or duplicate `app` tiles separately |

`render.yaml` and `docker-compose*.yml` live under `deploy/` only so the import UI does not treat the repo as an unconfigured multi-service monorepo.

`vercel.json` (Services format):

```json
"services": { "gateway": { "root": "app/", "framework": "fastapi", "entrypoint": "main:app" } }
```

### Architecture

```text
Browser → Vercel (FastAPI) → OPENAI_BASE_URL → Cloudflare Tunnel → Docker vLLM (:8080)
```

### Required Vercel environment variables

**Vercel → Project → Settings → Environment Variables** (Production + Preview):

| Variable | Required | Example |
| --- | --- | --- |
| `OPENAI_BASE_URL` | **Yes** | `https://private-llm.admindagency.com/v1` |
| `MODEL_NAME` | **Yes** | `qwen3.6-35b-a3b` |
| `API_KEY` or `VLLM_API_KEY` | If vLLM uses `--api-key` | Same secret as vLLM |
| `MAX_CONCURRENCY` | Optional | `8` |
| `MAX_QUEUE_DEPTH` | Optional | `64` |
| `MAX_OUTPUT_TOKENS` | Optional | `1024` |
| `REQUEST_TIMEOUT_SECONDS` | Optional | `60` |

Never set `http://127.0.0.1` on Vercel — it cannot reach your machine.

### New Vercel account / import from GitHub

1. **Add New → Project** → import `germainsafari/Private-AI`.
2. **Root Directory:** `./` (default).
3. If the UI shows **Application Preset: Services** and several detected apps, that is OK after this commit — root `vercel.json` defines **one** public service (`gateway`). Click **Deploy** (do not use “Import single project” only for `benchmarks`).
4. Add environment variables (below) **before** or right after first deploy, then **Redeploy**.

### After push or redeploy

```powershell
curl.exe https://private-llm.admindagency.com/v1/models
curl.exe https://<your-vercel-app>.vercel.app/api/health
```

Expect `backend_reachable: true` and model `qwen3.6-35b-a3b`.

### Local machine (must stay up for Vercel chat)

- Docker: `vllm` on **8080**
- Windows service: `cloudflared` (org tunnel)

Do **not** commit `app/.env`.

## Render (optional)

Docker deploy via [`deploy/render.yaml`](deploy/render.yaml) — not used by Vercel.

## LanguageTool

Separate hostname: `https://lt.admindagency.com` → `:8081`. See `docs/LANGUAGETOOL.md`.
