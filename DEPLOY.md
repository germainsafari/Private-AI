# Deploy checklist (Vercel + Tailscale Funnel)

## Important architecture note

`vercel.json` deploys the **FastAPI gateway** to Vercel. Vercel has **no GPU**. Chat only works if Vercel can reach your model over the public internet:

```text
Browser → Vercel (FastAPI) → OPENAI_BASE_URL → Tailscale Funnel → local vLLM (:8080)
```

**Safer / preferred for demos** (GPU stays local, Vercel optional):

```text
Browser → Tailscale Funnel → local FastAPI (:3000) → local vLLM (:8080)
```

In that preferred setup, either leave Vercel unused for inference, or point a simple frontend at the Funnel URL. Do **not** Funnel raw vLLM without an API key.

## Required Vercel environment variables

Set these in **Vercel → Project → Settings → Environment Variables** (Production + Preview):

| Variable | Required | Example / notes |
| --- | --- | --- |
| `OPENAI_BASE_URL` | **Yes** | `https://YOUR-DEVICE.YOUR-TAILNET.ts.net/v1` (Funnel → vLLM). Must end in `/v1`. **Never** `http://127.0.0.1:...` on Vercel. |
| `MODEL_NAME` | **Yes** | `Qwen/Qwen2.5-VL-7B-Instruct` |
| `API_KEY` or `VLLM_API_KEY` | Yes if vLLM has `--api-key` | Same secret you pass to vLLM |
| `MAX_CONCURRENCY` | Optional | `8` |
| `MAX_QUEUE_DEPTH` | Optional | `64` |
| `MAX_OUTPUT_TOKENS` | Optional | `1024` (keep lower on Vercel; function time limits) |
| `REQUEST_TIMEOUT_SECONDS` | Optional | `60` on Vercel (Hobby timeout is short) |
| `SYSTEM_PROMPT` | Optional | Neutral assistant text |

Do **not** commit `.env`. It is gitignored.

## Will the Vercel build succeed?

Yes, if:

1. `app/requirements.txt` installs (includes `fastapi`, `openai`, `pynvml`, `httpx`, …).
2. Imports succeed — `pynvml` is optional at runtime (GPU metrics degrade gracefully without NVML).
3. Env vars above are set so `/api/health` and chat can reach the backend.

### Runtime caveats (not build failures)

- **Serverless timeouts:** long streaming generations can be cut off on Hobby plans. Prefer Funnel → local gateway for demos.
- **Cold starts** add latency on first request.
- Without Funnel + correct `OPENAI_BASE_URL`, deploy succeeds but chat returns **502**.

## Pre-push verification

```powershell
# 1) Secrets not staged
git status
# confirm app/.env is NOT listed

# 2) Local gateway still works
# (vLLM in WSL + uvicorn on :3000)
```

Then push when ready:

```powershell
git add -A
git status   # review; exclude .env
git commit -m "Add inference orchestrator, metrics, benchmarks, and findings"
git push origin main
```

After push: open the Vercel deployment → confirm build green → hit `/api/health` → only then test chat (with Funnel up).
