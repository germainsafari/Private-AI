# Admind Chat — Private ChatGPT for your local GPU model

A modern, ChatGPT/Claude-style chat interface that talks to **any OpenAI-compatible API**, including your own self-hosted **vLLM / Ollama / LM Studio / Docker Model Runner** model. Built to work with a private GPU model over Tailscale — nothing leaves your network.

## Features

- Clean dark ChatGPT-style UI with streaming (SSE) responses
- Markdown rendering, syntax-highlighted code blocks, and "copy" buttons
- Multiple conversations persisted to `localStorage`
- New chat, delete chat, clear conversation, stop generation
- Auto-sizing composer, Enter to send, Shift+Enter for newline
- Works with any OpenAI-compatible endpoint (vLLM, Ollama, DMR, OpenAI, Groq, Together, …)

## Quick start (local Python)

```bash
cd app
pip install -r requirements.txt
# create .env (see Environment variables below)
uvicorn main:app --host 0.0.0.0 --port 8000
```

Then open [http://localhost:8000](http://localhost:8000).

### Example `.env` for a local vLLM GPU server over Tailscale

```env
OPENAI_BASE_URL=http://100.77.181.118:8080/v1
MODEL_NAME=Qwen/Qwen2.5-VL-32B-Instruct-AWQ
API_KEY=not-needed
```

### Example `.env` for Ollama

```env
OPENAI_BASE_URL=http://localhost:11434/v1
MODEL_NAME=llama3.1
API_KEY=ollama
```

### Example `.env` for OpenAI

```env
OPENAI_BASE_URL=https://api.openai.com/v1
MODEL_NAME=gpt-4o-mini
API_KEY=sk-...
```

## Environment variables

| Variable | Purpose |
|---|---|
| `OPENAI_BASE_URL` | OpenAI-compatible base URL (must end in `/v1`). Aliases: `BASE_URL`, `LLM_URL` |
| `MODEL_NAME` | Model id exactly as registered by your server. Aliases: `MODEL`, `LLM_MODEL` |
| `API_KEY` | API key. Use any non-empty placeholder for servers that ignore auth |
| `SYSTEM_PROMPT` | Optional. Overrides the default system prompt |

## API

- `POST /api/chat` — streaming (SSE) or non-streaming chat completion
  - Body: `{ "messages": [{"role":"user","content":"..."}], "stream": true, "temperature": 0.7, "max_tokens": 1024, "system": "optional override" }`
- `GET /api/models` — list models reported by the backend
- `GET /api/health` — `{ status, model, endpoint }`

## Run with Docker Compose

```bash
docker compose up --build
```

Edit `app/docker-compose.yml` to point at your backend.

## Project layout

```text
app/
  main.py            # FastAPI: streaming chat + static shell
  static/
    index.html
    css/style.css
    js/app.js
  Dockerfile
  docker-compose.yml
  requirements.txt
  .env.example
```

## Troubleshooting

- **UI says `offline`**: The backend can't reach `OPENAI_BASE_URL`. Test with `curl $OPENAI_BASE_URL/models`.
- **502 Model error**: Your model id in `MODEL_NAME` must match what your server reports. Check `GET $OPENAI_BASE_URL/models`.
- **Empty responses**: Some self-hosted models need a lower `temperature` or different stop tokens. Try `temperature: 0.3`.

## License

Use and modify as you see fit.
