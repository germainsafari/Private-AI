# Admind Private AI Chat

A self-hosted multimodal AI application powered by **Qwen2.5-VL**, **vLLM**, **FastAPI**, **Docker**, and an NVIDIA GPU.

The project runs model inference locally on a Windows 11 workstation through WSL 2, exposes an OpenAI-compatible API, and provides a lightweight web interface that can run locally or be deployed separately.

## Architecture

```text
User browser
     |
     v
Web interface
HTML, CSS and JavaScript
     |
     v
FastAPI application
     |
     v
vLLM OpenAI-compatible API
     |
     v
Qwen2.5-VL-7B-Instruct
     |
     v
NVIDIA RTX 5090
```

For remote access:

```text
Remote user or Vercel application
              |
              v
      Tailscale Funnel
              |
              v
       FastAPI backend
              |
              v
       Local vLLM server
              |
              v
         NVIDIA GPU
```

> [!IMPORTANT]
> Tailscale Funnel creates a publicly reachable endpoint. Always protect the application with authentication and never expose an unauthenticated vLLM endpoint directly to the public internet.

---

## Features

- Local GPU inference using an NVIDIA RTX 5090
- Vision and text generation with Qwen2.5-VL
- OpenAI-compatible API served by vLLM
- FastAPI backend for request validation and API proxying
- Image and text prompt support
- Static HTML, CSS and JavaScript interface
- Docker and Docker Compose support
- Persistent Hugging Face model cache
- Configurable model, context length and GPU memory budget
- Optional private access through Tailscale
- Optional public HTTPS access through Tailscale Funnel
- Compatible with external applications using the OpenAI API format

---

## Technology Stack

| Layer | Technology |
| --- | --- |
| Model | Qwen2.5-VL-7B-Instruct |
| Inference engine | vLLM |
| Backend | FastAPI |
| Frontend | HTML, CSS and JavaScript |
| Containers | Docker and Docker Compose |
| GPU environment | WSL 2 with NVIDIA CUDA support |
| Remote networking | Tailscale |
| Optional frontend hosting | Vercel |
| Optional backend hosting | Local GPU workstation |

---

## Repository Structure

```text
.
├── app/
│   ├── static/
│   │   ├── css/
│   │   │   └── style.css
│   │   ├── js/
│   │   │   └── app.js
│   │   └── index.html
│   ├── .dockerignore
│   ├── .env.example
│   ├── backend.env.example
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── main.py
│   └── requirements.txt
├── .gitignore
├── README.md
├── docker-compose.yml
├── render.yaml
├── requirements.txt
└── vercel.json
```

### Important files

| File | Purpose |
| --- | --- |
| `app/main.py` | FastAPI application and vLLM proxy |
| `app/static/index.html` | Main web interface |
| `app/static/css/style.css` | Frontend styling |
| `app/static/js/app.js` | Browser-side API requests |
| `app/Dockerfile` | Container definition for the web application |
| `app/docker-compose.yml` | Application-specific Compose configuration |
| `docker-compose.yml` | Root Compose configuration |
| `.env.example` | Example environment variables |
| `vercel.json` | Optional Vercel configuration |
| `render.yaml` | Optional Render configuration |

---

# Prerequisites

Before beginning, install the following:

- Windows 11
- NVIDIA GPU with a current driver
- WSL 2
- Ubuntu 24.04 or another supported Ubuntu release
- Docker Desktop with the WSL 2 backend
- Git
- Tailscale, only when remote access is required
- A Hugging Face account, only when the selected model requires authentication

Verify that the GPU works on Windows:

```powershell
nvidia-smi
```

You should see your NVIDIA GPU, driver version, CUDA compatibility version, GPU utilization and VRAM usage.

---

# 1. Install WSL 2

Open **PowerShell as Administrator**:

```powershell
wsl --install -d Ubuntu-24.04
```

Restart Windows when prompted.

Update WSL:

```powershell
wsl --update
```

Set WSL 2 as the default:

```powershell
wsl --set-default-version 2
```

Verify the installation:

```powershell
wsl --list --verbose
```

Expected output:

```text
NAME              STATE      VERSION
Ubuntu-24.04      Running    2
docker-desktop    Running    2
```

Start Ubuntu:

```powershell
wsl -d Ubuntu-24.04
```

Inside Ubuntu, install the basic development packages:

```bash
sudo apt update
sudo apt upgrade -y

sudo apt install -y \
  git \
  curl \
  wget \
  jq \
  build-essential \
  python3 \
  python3-pip \
  python3-venv
```

Verify that the Windows GPU is visible from WSL:

```bash
nvidia-smi
```

> [!NOTE]
> Do not install a second NVIDIA Windows display driver inside WSL. WSL uses the NVIDIA driver installed on the Windows host.

---

# 2. Configure Docker Desktop

Open Docker Desktop and configure the following:

1. Open **Settings**
2. Select **General**
3. Enable **Use the WSL 2 based engine**
4. Select **Resources**
5. Select **WSL Integration**
6. Enable integration for `Ubuntu-24.04`
7. Apply the changes and restart Docker Desktop

Verify Docker from Ubuntu:

```bash
docker version
```

Verify that a Docker container can access the GPU:

```bash
docker run --rm --gpus all \
  nvidia/cuda:12.8.1-base-ubuntu22.04 \
  nvidia-smi
```

The container should display the RTX 5090.

---

# 3. Clone the Repository

Inside WSL:

```bash
cd ~
git clone YOUR_REPOSITORY_URL
cd YOUR_REPOSITORY_NAME
```

Replace the placeholders with the real GitHub repository URL and folder name.

For better Docker and Linux filesystem performance, keep the repository inside the WSL filesystem, for example:

```text
/home/your-user/YOUR_REPOSITORY_NAME
```

Avoid running large model workloads directly from:

```text
/mnt/c/Users/...
```

You may still open the WSL project in VS Code:

```bash
code .
```

---

# 4. Configure Environment Variables

Create the environment file from the provided example:

```bash
cp app/.env.example app/.env
```

Open it:

```bash
nano app/.env
```

Example configuration:

```env
VLLM_BASE_URL=http://host.docker.internal:8080/v1
MODEL_NAME=Qwen/Qwen2.5-VL-7B-Instruct
VLLM_API_KEY=replace-with-a-long-random-secret
APP_API_KEY=replace-with-another-long-random-secret
REQUEST_TIMEOUT_SECONDS=300
MAX_OUTPUT_TOKENS=1024
```

Generate secure random values:

```bash
openssl rand -hex 32
```

Run the command twice and use different values for `VLLM_API_KEY` and `APP_API_KEY`.

### Environment variable descriptions

| Variable | Description |
| --- | --- |
| `VLLM_BASE_URL` | Base URL of the vLLM OpenAI-compatible API |
| `MODEL_NAME` | Model identifier exposed by vLLM |
| `VLLM_API_KEY` | Secret used by the backend to authenticate with vLLM |
| `APP_API_KEY` | Optional secret used by clients to access the FastAPI backend |
| `REQUEST_TIMEOUT_SECONDS` | Maximum time allowed for model generation |
| `MAX_OUTPUT_TOKENS` | Maximum output token limit enforced by the application |

> [!WARNING]
> Never commit `.env` files, Hugging Face tokens or API keys to GitHub.

---

# 5. Create a Persistent Model Cache

Inside WSL:

```bash
mkdir -p ~/.cache/huggingface
```

This directory stores downloaded model files so that they are not downloaded every time the container restarts.

Check its size:

```bash
du -sh ~/.cache/huggingface
```

If the model requires Hugging Face authentication:

```bash
export HF_TOKEN="your-hugging-face-token"
```

For repeated use, store it in a local environment file that is excluded from Git:

```bash
echo 'HF_TOKEN=your-hugging-face-token' > .env.vllm
chmod 600 .env.vllm
```

Make sure `.env.vllm` appears in `.gitignore`.

---

# 6. Recommended Method: Run vLLM with Docker

Using Docker is the recommended approach because it isolates the vLLM, PyTorch and CUDA dependencies from the rest of the system.

Pull the official vLLM image:

```bash
docker pull vllm/vllm-openai:latest
```

Start Qwen2.5-VL:

```bash
docker run -d \
  --name vllm-qwen-vl \
  --gpus all \
  --ipc=host \
  --restart unless-stopped \
  -p 8080:8000 \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  --env-file .env.vllm \
  vllm/vllm-openai:latest \
  --model Qwen/Qwen2.5-VL-7B-Instruct \
  --served-model-name Qwen/Qwen2.5-VL-7B-Instruct \
  --host 0.0.0.0 \
  --port 8000 \
  --dtype auto \
  --max-model-len 4096 \
  --gpu-memory-utilization 0.60 \
  --limit-mm-per-prompt '{"image":4}' \
  --api-key replace-with-a-long-random-secret
```

The port mapping means:

```text
Host port 8080 -> Container port 8000
```

The API is therefore available from the host at:

```text
http://127.0.0.1:8080/v1
```

### Important vLLM arguments

| Argument | Purpose |
| --- | --- |
| `--gpus all` | Gives the container access to the GPU |
| `--ipc=host` | Provides sufficient shared memory for PyTorch |
| `-p 8080:8000` | Exposes container port 8000 through host port 8080 |
| `--model` | Hugging Face model identifier |
| `--served-model-name` | Model name expected in API requests |
| `--max-model-len 4096` | Limits the maximum sequence length |
| `--gpu-memory-utilization 0.60` | Sets the fraction of GPU memory available to this vLLM instance |
| `--limit-mm-per-prompt` | Limits the number of multimodal inputs in one prompt |
| `--api-key` | Protects the vLLM API with a bearer token |
| `--restart unless-stopped` | Restarts the container after Docker or system restarts |

> [!NOTE]
> `--gpu-memory-utilization 0.60` defines a memory budget for the vLLM instance. It does not guarantee that exactly 60% of VRAM will always be physically occupied.

Follow the startup logs:

```bash
docker logs -f vllm-qwen-vl
```

Stop following the logs with `Ctrl+C`.

Check the container:

```bash
docker ps
```

Check its resource consumption:

```bash
docker stats vllm-qwen-vl
```

Check GPU utilization:

```bash
watch -n 1 nvidia-smi
```

---

# 7. Test the vLLM API

List available models:

```bash
curl http://127.0.0.1:8080/v1/models \
  -H "Authorization: Bearer replace-with-a-long-random-secret"
```

Test text generation:

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H "Authorization: Bearer replace-with-a-long-random-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen2.5-VL-7B-Instruct",
    "messages": [
      {
        "role": "user",
        "content": "Explain continuous batching in three sentences."
      }
    ],
    "temperature": 0.2,
    "max_tokens": 200
  }'
```

A successful response should contain:

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "..."
      }
    }
  ]
}
```

---

# 8. Test Vision Input

Qwen2.5-VL supports messages containing text and images.

Example using an image URL:

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H "Authorization: Bearer replace-with-a-long-random-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen2.5-VL-7B-Instruct",
    "messages": [
      {
        "role": "user",
        "content": [
          {
            "type": "text",
            "text": "Describe this image and identify its most important elements."
          },
          {
            "type": "image_url",
            "image_url": {
              "url": "https://example.com/image.jpg"
            }
          }
        ]
      }
    ],
    "temperature": 0.2,
    "max_tokens": 500
  }'
```

Your FastAPI backend may also convert uploaded images to Base64 data URLs before sending them to vLLM.

Example image content:

```json
{
  "type": "image_url",
  "image_url": {
    "url": "data:image/jpeg;base64,BASE64_IMAGE_DATA"
  }
}
```

---

# 9. Run the FastAPI Application with Docker Compose

From the repository root:

```bash
docker compose up -d --build
```

Check the services:

```bash
docker compose ps
```

Follow all logs:

```bash
docker compose logs -f
```

Follow only the application logs:

```bash
docker compose logs -f app
```

Open the application:

```text
http://127.0.0.1:3000
```

The exact port depends on the mapping in `docker-compose.yml`.

Stop the application:

```bash
docker compose stop
```

Stop and remove its containers:

```bash
docker compose down
```

Rebuild after changing dependencies or the Dockerfile:

```bash
docker compose up -d --build
```

---

# 10. Run the FastAPI Application Without Docker

Docker is recommended, but the web application can also run directly in a Python virtual environment.

Inside WSL:

```bash
cd app
python3 -m venv .venv
source .venv/bin/activate
```

Upgrade pip and install the dependencies:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Load the environment variables:

```bash
set -a
source .env
set +a
```

Start FastAPI:

```bash
python -m uvicorn main:app \
  --host 0.0.0.0 \
  --port 3000 \
  --reload
```

Open:

```text
http://127.0.0.1:3000
```

Deactivate the environment when finished:

```bash
deactivate
```

---

# 11. Native vLLM Installation Without Docker

This method is optional. Docker is usually easier because vLLM, PyTorch and CUDA versions must be compatible.

Create a dedicated environment:

```bash
python3 -m venv ~/vllm-env
source ~/vllm-env/bin/activate
```

Upgrade pip:

```bash
python -m pip install --upgrade pip
```

Install vLLM:

```bash
pip install vllm
```

Start the server:

```bash
vllm serve Qwen/Qwen2.5-VL-7B-Instruct \
  --served-model-name Qwen/Qwen2.5-VL-7B-Instruct \
  --host 0.0.0.0 \
  --port 8080 \
  --dtype auto \
  --max-model-len 4096 \
  --gpu-memory-utilization 0.60 \
  --limit-mm-per-prompt '{"image":4}' \
  --api-key replace-with-a-long-random-secret
```

Keep this terminal open while the server is running.

Stop the server with:

```text
Ctrl+C
```

---

# 12. Run a Second Model on Another Port

Multiple models can be run on the same GPU when their combined model weights, KV caches and runtime memory fit inside VRAM.

Example second model:

```bash
docker run -d \
  --name vllm-qwen-small \
  --gpus all \
  --ipc=host \
  --restart unless-stopped \
  -p 8081:8000 \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  --env-file .env.vllm \
  vllm/vllm-openai:latest \
  --model Qwen/Qwen2.5-1.5B-Instruct \
  --served-model-name qwen-1.5b \
  --host 0.0.0.0 \
  --port 8000 \
  --dtype auto \
  --max-model-len 4096 \
  --gpu-memory-utilization 0.20 \
  --api-key replace-with-a-long-random-secret
```

The two APIs will be available at:

```text
Qwen2.5-VL 7B: http://127.0.0.1:8080/v1
Qwen2.5 1.5B:  http://127.0.0.1:8081/v1
```

Check both containers:

```bash
docker ps
```

Check combined VRAM usage:

```bash
watch -n 1 nvidia-smi
```

> [!WARNING]
> The sum of the configured GPU memory fractions should leave headroom for CUDA, Windows graphics and temporary operations. Start conservatively and increase allocations only after measuring actual usage.

---

# 13. Remote Access with Tailscale

There are two different Tailscale exposure modes.

## Tailscale Serve

Use Tailscale Serve when only authenticated devices in your tailnet should access the application.

On Windows PowerShell:

```powershell
tailscale serve http://127.0.0.1:3000
```

Check the configuration:

```powershell
tailscale serve status
```

Reset it:

```powershell
tailscale serve reset
```

This option is private, but a Vercel deployment cannot normally reach it unless it has access to your tailnet.

## Tailscale Funnel

Use Tailscale Funnel only when an external service such as Vercel must reach your local backend over the public internet.

First expose the FastAPI application:

```powershell
tailscale funnel http://127.0.0.1:3000
```

Check its status:

```powershell
tailscale funnel status
```

Copy the generated HTTPS address.

It will look similar to:

```text
https://your-device.your-tailnet.ts.net
```

Reset the Funnel configuration:

```powershell
tailscale funnel reset
```

> [!CAUTION]
> Funnel is public. Expose the FastAPI application rather than exposing vLLM directly. The FastAPI layer should validate authentication, file size, content type, request limits and output limits.

---

# 14. Connect a Vercel Frontend

Set the following variables in:

```text
Vercel Dashboard
-> Project
-> Settings
-> Environment Variables
```

| Variable | Example |
| --- | --- |
| `BACKEND_BASE_URL` | `https://your-device.your-tailnet.ts.net` |
| `BACKEND_API_KEY` | Your FastAPI application key |
| `MODEL_NAME` | `Qwen/Qwen2.5-VL-7B-Instruct` |

Do not expose `VLLM_API_KEY` in browser-side JavaScript.

The correct request flow is:

```text
Browser
   |
   v
Vercel server-side API route
   |
   v
Tailscale Funnel
   |
   v
FastAPI backend
   |
   v
vLLM
```

Avoid this architecture:

```text
Browser
   |
   v
Public vLLM endpoint
```

It would expose the model endpoint and potentially reveal its API key.

After adding or changing Vercel environment variables, redeploy the application.

---

# 15. Container Management

List running containers:

```bash
docker ps
```

List all containers:

```bash
docker ps -a
```

List downloaded images:

```bash
docker image ls
```

View the vLLM logs:

```bash
docker logs --tail 100 vllm-qwen-vl
```

Follow logs:

```bash
docker logs -f vllm-qwen-vl
```

Stop the model and release its VRAM:

```bash
docker stop vllm-qwen-vl
```

Start it again:

```bash
docker start vllm-qwen-vl
```

Restart it:

```bash
docker restart vllm-qwen-vl
```

Remove the stopped container:

```bash
docker rm vllm-qwen-vl
```

Inspect the container configuration:

```bash
docker inspect vllm-qwen-vl
```

See the container startup command:

```bash
docker inspect vllm-qwen-vl \
  --format='{{json .Config.Cmd}}' | jq
```

See its mounted directories:

```bash
docker inspect vllm-qwen-vl \
  --format='{{json .Mounts}}' | jq
```

---

# 16. GPU and VRAM Management

Check GPU utilization:

```powershell
nvidia-smi
```

On Windows using the WDDM driver model, individual CUDA process memory may appear as `N/A`. Docker and WSL workloads may therefore be easier to identify through Docker container inspection.

Check running containers:

```powershell
docker ps
```

Stop a suspected model container:

```powershell
docker stop vllm-qwen-vl
```

Stop every running Docker container from PowerShell:

```powershell
docker ps -q | ForEach-Object {
    docker stop $_
}
```

Shut down WSL completely:

```powershell
wsl --shutdown
```

Exit Docker Desktop if necessary, wait several seconds, and check again:

```powershell
nvidia-smi
```

A small amount of VRAM may remain in use by Windows, browsers and desktop composition. The goal is not necessarily exactly `0 MiB`, but model-sized allocations should be released.

If a GPU process remains stuck after its application, container and WSL instance have all been stopped, restart Windows:

```powershell
shutdown /r /t 0
```

---

# 17. Troubleshooting

## `ValueError: No available memory for cache blocks`

vLLM does not have enough VRAM for model weights, runtime memory and KV-cache blocks.

Try one or more of the following:

1. Stop other GPU containers:

   ```bash
   docker ps
   docker stop CONTAINER_NAME
   ```

2. Reduce the maximum context length:

   ```text
   --max-model-len 2048
   ```

3. Increase the allowed memory fraction carefully:

   ```text
   --gpu-memory-utilization 0.70
   ```

4. Use a smaller model.

5. Use a supported quantized model.

6. Close GPU-heavy applications such as games, rendering tools or multiple hardware-accelerated browser windows.

---

## CUDA out-of-memory error

Check the GPU:

```bash
nvidia-smi
```

Check Docker containers:

```bash
docker ps
```

Stop unused models:

```bash
docker stop CONTAINER_NAME
```

Reduce:

```text
--max-model-len
--gpu-memory-utilization
maximum image resolution
maximum images per prompt
maximum output tokens
```

---

## Docker cannot access the GPU

Confirm that:

- The NVIDIA Windows driver is current
- WSL is updated
- Docker Desktop uses the WSL 2 backend
- WSL integration is enabled for Ubuntu
- `nvidia-smi` works inside WSL

Update WSL:

```powershell
wsl --update
wsl --shutdown
```

Restart Docker Desktop and test again:

```bash
docker run --rm --gpus all \
  nvidia/cuda:12.8.1-base-ubuntu22.04 \
  nvidia-smi
```

---

## Port already in use

Check port `8080` on Windows:

```powershell
Get-NetTCPConnection -LocalPort 8080
```

Show the owning process:

```powershell
Get-Process -Id (
    Get-NetTCPConnection -LocalPort 8080
).OwningProcess
```

Stop the process only after confirming it is safe:

```powershell
Stop-Process -Id PROCESS_ID
```

Alternatively, expose vLLM on another host port:

```bash
-p 8081:8000
```

---

## `listener already exists`

Reset the previous Tailscale configuration:

```powershell
tailscale serve reset
tailscale funnel reset
```

Then create the required route again.

---

## The backend cannot reach vLLM

When FastAPI runs directly inside WSL:

```env
VLLM_BASE_URL=http://127.0.0.1:8080/v1
```

When FastAPI runs inside Docker and vLLM is published on the host:

```env
VLLM_BASE_URL=http://host.docker.internal:8080/v1
```

When both services run in the same Docker Compose network:

```env
VLLM_BASE_URL=http://vllm:8000/v1
```

The value depends on where each process is running.

---

## The model downloads every time

Confirm that the Hugging Face cache is mounted:

```bash
-v ~/.cache/huggingface:/root/.cache/huggingface
```

Inspect the mount:

```bash
docker inspect vllm-qwen-vl \
  --format='{{json .Mounts}}' | jq
```

Check the cache:

```bash
du -sh ~/.cache/huggingface
```

---

## The model starts but requests fail

Check the model name exposed by the server:

```bash
curl http://127.0.0.1:8080/v1/models \
  -H "Authorization: Bearer replace-with-a-long-random-secret"
```

The request's `model` field must match one of the returned model identifiers.

---

# 18. Security Recommendations

Before allowing other users to access the application:

- Protect both FastAPI and vLLM with different API keys
- Never include backend secrets in browser-side JavaScript
- Expose FastAPI rather than exposing vLLM directly
- Add request-rate limiting
- Restrict uploaded file size
- Validate MIME types and file extensions
- Reject unsupported image formats
- Add request and generation timeouts
- Limit prompt length and output tokens
- Avoid logging sensitive prompts and images
- Keep Tailscale Funnel disabled when it is not required
- Use Tailscale Serve for private access whenever possible
- Rotate API keys if they are accidentally committed
- Keep Docker, WSL, NVIDIA drivers and vLLM updated
- Pin container image versions for reproducible deployments

---

# 19. Useful Daily Commands

Start the model:

```bash
docker start vllm-qwen-vl
```

Start the application:

```bash
docker compose up -d
```

Check services:

```bash
docker ps
docker compose ps
nvidia-smi
```

Follow model logs:

```bash
docker logs -f vllm-qwen-vl
```

Follow application logs:

```bash
docker compose logs -f
```

Stop the application:

```bash
docker compose down
```

Stop the model:

```bash
docker stop vllm-qwen-vl
```

Free WSL resources from Windows:

```powershell
wsl --shutdown
```

---

# 20. Clean Up Docker Resources

Review Docker disk usage:

```bash
docker system df
```

Remove stopped containers, unused networks and dangling images:

```bash
docker system prune
```

Remove all unused images as well:

```bash
docker system prune -a
```

> [!WARNING]
> `docker system prune -a` can remove downloaded images that are not currently attached to a container. They may need to be downloaded again.

The Hugging Face cache mounted from `~/.cache/huggingface` is separate from Docker image storage and should not be removed by a standard Docker prune.

---

# 21. Planned Inference Engineering Experiments

This project can be used to practise production inference engineering concepts.

Suggested experiments:

- Measure time to first token
- Measure inter-token latency
- Measure output tokens per second
- Compare P50, P95 and P99 latency
- Increase request concurrency
- Compare aggregate throughput with per-user throughput
- Compare text-only and vision requests
- Test different input and output lengths
- Compare quantized and non-quantized models
- Test prefix caching
- Test continuous batching under concurrent traffic
- Compare different GPU memory utilization values
- Run two model servers on different ports
- Observe KV-cache pressure
- Monitor GPU utilization, power usage and VRAM
- Define latency service-level objectives
- Calculate goodput under latency constraints

Example experiment matrix:

| Test | Input tokens | Output tokens | Concurrency |
| --- | ---: | ---: | ---: |
| Baseline | 128 | 128 | 1 |
| Light traffic | 512 | 128 | 4 |
| Medium traffic | 512 | 256 | 8 |
| High traffic | 512 | 256 | 16 |
| Long prompt | 4096 | 256 | 4 |
| Vision request | Image + prompt | 256 | 4 |

Change only one major variable at a time so that benchmark results remain interpretable.

---

# License

Add the appropriate license for the repository.

For a private internal Admind project, access and usage should follow the company's internal policies and client confidentiality requirements.
