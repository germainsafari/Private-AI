# Deploy checklist

> **Easier pattern (like Ppttx maker):** put **only the UI** on Vercel (or skip Vercel) and run **FastAPI + ingest** on your PC behind a Cloudflare tunnel (`:3000`). See [`docs/VERCEL_LESSONS_FROM_DECK_BUILDER.md`](docs/VERCEL_LESSONS_FROM_DECK_BUILDER.md).

## Recommended (deck-builder style)

| Host | Service | Tunnel |
| --- | --- | --- |
| Office PC | `uvicorn main:app` on **:3000** | `private-ai-api.admindagency.com` → `http://localhost:3000` |
| Office PC | vLLM Docker **:8080** | `private-llm.admindagency.com/v1` (existing) |
| Vercel *(optional)* | Static/Next UI only | Set `<meta name="api-base" content="https://private-ai-api...">` |

```powershell
cd app
pip install -r requirements.txt
# .env: OPENAI_BASE_URL=https://private-llm.admindagency.com/v1
uvicorn main:app --host 127.0.0.1 --port 3000
```

No PyMuPDF on Vercel, no 4.5 MB edge limit on uploads, no multi-service import UI.

## Vercel (all-in gateway)

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
| `MAX_UPLOAD_MB` | Optional | `25` (document ingest) |
| `LLM_DISABLE_THINKING` | Recommended | `1` (Qwen3.x) |
| `VLLM_MAX_IMAGES_PER_PROMPT` | Optional | `4` (match vLLM `--limit-mm-per-prompt`) |
| `DECK_ENGINE_URL` | For Slides | `https://slides-api.admindagency.com` |
| `DECK_ENGINE_TOKEN_SECRET` | For Slides | same value as `ENGINE_TOKEN_SECRET` in Ppttx maker `infra/.env` (secret) |
| `IMAGE_BASE_URL`, `IMAGE_API_KEY`, `IMAGE_MODEL` | Optional | OpenAI-compatible image API; enables the Image output |
| `MAX_VISION_IMAGES` | Optional | `60` (images transcribed per file) |
| `MAX_VISION_PAGES` | Optional | `60` (PDF pages rasterized per file) |
| `MAX_EMBEDDED_IMAGES` | Optional | `60` (DOCX/PPTX embedded pictures) |
| `PDF_IMAGE_COVERAGE_THRESHOLD` | Optional | `0.55` |

Never set `http://127.0.0.1` on Vercel — it cannot reach your machine.

### Document uploads (`POST /api/ingest`)

**On Vercel:** `.pdf`, `.docx`, `.pptx`, `.xlsx`, `.txt`, `.md`, `.json`, `.csv` — PyMuPDF, python-docx, python-pptx, openpyxl.

**Local only** (LibreOffice): `.doc`, `.odt`, `.rtf` — set `SOFFICE_PATH` on the host running uvicorn (not available in Vercel serverless).

Details: [`docs/DOCUMENT_INGEST.md`](docs/DOCUMENT_INGEST.md).

**Hobby plan:** request bodies are ~4.5 MB — use smaller files or lower `MAX_UPLOAD_MB` if ingest returns 413.

### Verify after deploy

Current production hostname (after the env fix + redeploy): **`https://private-ai-mu-woad.vercel.app`**. Prefer this over older `*.vercel.app` aliases that may still serve a deployment with stale env.

```powershell
$APP = "https://private-ai-mu-woad.vercel.app"
curl.exe "$APP/api/health"
curl.exe "$APP/api/create/capabilities"
curl.exe "$APP/api/ingest/formats"
curl.exe -F "files=@README.md" "$APP/api/ingest"
```

Expect `backend_reachable: true`, model `qwen3.6-35b-a3b`, `documents: true`, and with deck vars set `presentation: true`. Then in the UI: create a short PDF, attach a DOCX and ask for an edit, or ask for slides.

Deep dive on create/edit: [`docs/FILE_GENERATION.md`](docs/FILE_GENERATION.md).

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
