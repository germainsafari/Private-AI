# Polski — Polish practice with Docker Model Runner

A small web app that generates Polish reading exercises (short passages with drag-and-drop words), checks your answers, and tracks a simple **day streak** in the browser. The backend uses the **OpenAI-compatible API** exposed by [Docker Model Runner](https://docs.docker.com/ai/model-runner/) (DMR), e.g. the **`ai/gemma4:latest`** model.

## Prerequisites

- **Docker Desktop** with [Model Runner](https://docs.docker.com/ai/model-runner/) enabled  
- For **Docker Compose** with the `models:` block: **Compose v2.38+** (see [AI models in Compose](https://docs.docker.com/ai/compose/models-and-compose/))  
- For **local Python** runs: host access to DMR on **TCP port 12434** (enable in Docker Desktop AI / Model Runner settings, or use the [Desktop CLI](https://docs.docker.com/desktop/features/desktop-cli/) as documented by Docker)

## Run with Docker Compose (recommended)

**Option A — project root** (`Private AI`, where this README is):

```bash
docker compose up --build
```

**Option B — `app` folder** (where `Dockerfile` lives):

```bash
cd app
docker compose up --build
```

If you see `no configuration file provided`, you are in a folder without `docker-compose.yml`; use **Option A** from the repo root or **Option B** after `cd app`.

Open [http://localhost:8000](http://localhost:8000). Compose binds the **`llm`** model and injects **`OPENAI_BASE_URL`** and **`MODEL_NAME`** into the `app` service — you do not need to set `host.docker.internal` manually.

To change the model image, edit `models.llm.model` in `app/docker-compose.yml` (for example another tag of Gemma or a different DMR-supported model).

## Run locally with Python

Useful while editing the API or static files:

```bash
cd app
# Copy .env.example to .env (e.g. copy .env.example .env on Windows, or cp on Unix)
pip install -r requirements.txt
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Point **`.env`** at your local DMR OpenAI base URL. The defaults in **`.env.example`** assume **`http://localhost:12434/engines/gemma4.cpp/v1`** and **`MODEL_NAME=ai/gemma4:latest`**. Adjust the path if your DMR setup uses a different engine segment (see [DMR REST API](https://docs.docker.com/ai/model-runner/api-reference/)).

## Environment variables

| Variable | Purpose |
|----------|---------|
| `OPENAI_BASE_URL` | OpenAI SDK base URL (DMR: typically ends with `/engines/.../v1`). Injected by Compose when using `models:` |
| `MODEL_NAME` | Model id (e.g. `ai/gemma4:latest`). Injected by Compose when using `models:` |
| `BASE_URL` | Legacy alias for `OPENAI_BASE_URL` |
| `LLM_URL` / `LLM_MODEL` | Fallback names if you use Compose **short** model syntax |
| `API_KEY` | Optional. DMR ignores auth; the app substitutes a placeholder if this is empty so the OpenAI client does not error |

Optional **`backend.env`**: copy `app/backend.env.example` to `backend.env` and add `env_file: - backend.env` under the `app` service if you want extra variables without changing Compose.

## Project layout

```text
app/
  main.py           # FastAPI API + static app shell
  static/           # HTML, CSS, JS
  Dockerfile
  docker-compose.yml
  requirements.txt
  .env.example
```

## Deploy on [Render](https://render.com)

**Important:** [Docker Model Runner](https://docs.docker.com/ai/model-runner/) runs on **your machine** (or a server you control). **Render cannot reach `localhost:12434` or your Docker Desktop model.** On Render you must point the app at a **public OpenAI-compatible HTTPS API** (for example [OpenAI](https://platform.openai.com/), [Groq](https://console.groq.com/), [Together](https://www.together.ai/), or any self-hosted API you expose with TLS).

1. Push this repo to GitHub/GitLab/Bitbucket.
2. In Render: **New** → **Blueprint** (or **Web Service** with **Docker**).
3. Connect the repo. If you use the included `render.yaml`, Render picks `app/Dockerfile` and `dockerContext: app`.
4. In **Environment**, set:
   - **`API_KEY`** — your provider’s secret key (mark secret).
   - **`OPENAI_BASE_URL`** — e.g. `https://api.openai.com/v1` (OpenAI), or your provider’s base URL.
   - **`MODEL_NAME`** — e.g. `gpt-4o-mini` (must exist on that API).
5. Deploy. The Dockerfile listens on **`PORT`** (Render sets this automatically).

Defaults in `render.yaml` assume OpenAI + `gpt-4o-mini`; change `OPENAI_BASE_URL` / `MODEL_NAME` for other hosts. The app’s health check is **`GET /api/health`**.

## Troubleshooting

- **Lessons show “offline” or errors**: Confirm DMR is running and reachable — from the host, `GET` [http://localhost:12434/engines/v1/models](http://localhost:12434/engines/v1/models) should respond when TCP access is enabled.  
- **Compose errors on `models:`**: Upgrade Docker Desktop / Compose to a version that supports the [Compose `models` key](https://docs.docker.com/reference/compose-file/models/).  
- **Wrong engine path**: If chat calls fail, try the generic base `.../engines/v1` instead of `.../engines/gemma4.cpp/v1` in `.env` (local run only).

## License

Use and modify for your own learning and deployment as you see fit.
