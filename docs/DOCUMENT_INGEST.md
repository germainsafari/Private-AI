# Document ingest

The chat UI sends **office files and PDFs** to `POST /api/ingest` on the FastAPI gateway. Parsing is **text-first**; **vision** is used for photos, scanned PDF pages, image-heavy slides, and embedded pictures in Word/PowerPoint.

## Supported formats

| Extension | Method |
| --- | --- |
| `.txt`, `.md`, `.csv`, `.json` | UTF-8 / charset detection |
| `.pdf` | PyMuPDF text; low-text or image-heavy pages → PNG for vision |
| `.docx` | Paragraphs, tables; images under `word/media/` → vision |
| `.pptx` | Slide text, tables, speaker notes; `ppt/media/` → vision |
| `.xlsx` | Sheet rows via openpyxl |
| `.doc`, `.odt`, `.rtf` | **Local only:** LibreOffice headless → `.txt` (set `SOFFICE_PATH`) |

Vercel serverless **does not** run LibreOffice; legacy Office formats work on your machine or any host where `soffice` is installed.

## Flow

```text
Browser attach → POST /api/ingest
   1. parse every file (one unreadable file becomes a warning, not an error)
   2. images / scanned pages / embedded pictures → vision model, 4 per request
      (VLLM_MAX_IMAGES_PER_PROMPT) → transcribed to text in place
   3. if the combined text exceeds the model context, it is condensed part by
      part into dense notes (facts, numbers and tables kept), not truncated
   → combined_text (text only, images[] is empty)
→ POST /api/chat
   history is fitted to the context: older document dumps and images are
   replaced by short notes, oldest turns dropped last; max_tokens is clamped
```

The model therefore never receives more images than vLLM allows or more tokens
than `max_model_len`, regardless of how many files or pages are attached. More
images than `VLLM_MAX_IMAGES_PER_PROMPT` attached directly in the chat box, or
images over 4 MB, are routed through ingest automatically.

## Configuration

| Variable | Default | Purpose |
| --- | ---: | --- |
| `MAX_UPLOAD_MB` | 25 | Per-file size limit |
| `MAX_EXTRACT_CHARS` | 3000000 | Safety ceiling on raw extracted text |
| `VLLM_MAX_IMAGES_PER_PROMPT` | 4 | Must match vLLM `--limit-mm-per-prompt` |
| `MAX_VISION_IMAGES` | 60 | Images transcribed per file |
| `MAX_VISION_PAGES` | 60 | Max PDF pages rasterized per file |
| `MAX_EMBEDDED_IMAGES` | 60 | Max embedded images per DOCX/PPTX (deduped, icons < 3 KB skipped) |
| `MODEL_CONTEXT_TOKENS` | from vLLM | Override `max_model_len` detection |
| `CHARS_PER_TOKEN` | 2.5 | Conservative size estimate used for budgeting |
| `CONDENSE_CHUNK_CHARS` | 40000 | Chunk size for condensing long documents |
| `MIN_PDF_TEXT_CHARS` | 40 | Below this → consider vision for page |
| `PDF_IMAGE_COVERAGE_THRESHOLD` | 0.55 | Image-heavy page → also vision |
| `SOFFICE_PATH` | — | Path to `soffice.exe` for `.doc`/`.odt`/`.rtf` |

## API

- `POST /api/ingest` — multipart field `files` (repeat for multiple files)
- `GET /api/ingest/formats` — limits + `allowed_extensions`
- `/api/health` — `documents`, `libreoffice`, `allowed_extensions`

## Vercel limits

Hobby plans cap **request body size** (~4.5 MB). Keep uploads small on production, or run the gateway locally for large briefs.

## Tests

```powershell
cd app
python -m pytest tests/test_ingest.py -q
```