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
Browser attach → POST /api/ingest → combined_text + images[]
       → POST /api/chat → vLLM (Qwen VL) reads text + image_url parts
```

## Configuration

| Variable | Default | Purpose |
| --- | ---: | --- |
| `MAX_UPLOAD_MB` | 25 | Per-file size limit |
| `MAX_EXTRACT_CHARS` | 80000 | Truncate extracted text |
| `MAX_VISION_IMAGES` | 12 | Total vision images per ingest batch |
| `MAX_VISION_PAGES` | 12 | Max PDF pages rasterized per file |
| `MAX_EMBEDDED_IMAGES` | 8 | Max embedded images per DOCX/PPTX |
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