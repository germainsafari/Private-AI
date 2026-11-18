# File generation and editing

The chat can create and edit files, and they download straight from the conversation.

## How a request flows

1. **Output chips**: *Auto*, *Chat*, *PDF*, *Word*, *Slides*, *Edit file* and *Image*. In Auto mode a keyword check (English and Polish) decides whether the message might want a file. Ordinary questions skip it entirely.
2. `POST /api/create` (multipart: `prompt`, `action`, `history`, `files`, optional `previous_markdown` / `previous_kind`). The server classifies the intent with a JSON-schema call (`Intent`). If no file is wanted it answers `{"type":"chat"}` and the browser falls back to normal chat.
3. Progress streams as SSE events: `progress`, `warning`, `file` (base64 file plus summary and markdown) and `deck_job`.
4. The browser stores files in **IndexedDB**. The chat history only keeps a summary and a reference, so files stay on the user's machine.

## Outputs

| Action | Pipeline | Files |
| --- | --- | --- |
| `pdf_report` | `ReportOutline`, then one `SectionContent` per section (written in parallel, `SECTION_CONCURRENCY`), then `render_pdf.py` | `app/render_pdf.py`, `app/charts.py` |
| `docx_document` | same pipeline, rendered by `render_docx.py` | `app/render_docx.py` |
| `presentation` | brief and context sent to the deck engine; the browser polls `/api/deck/{id}` | `app/deck_client.py` |
| `edit_file` | `EditPlan` (find/replace, appended paragraphs, cell edits, full text), applied by `file_edit.py` | `app/file_edit.py` |
| `image` | `images.py`, which calls an OpenAI-compatible `/images/generations` endpoint | `app/images.py` |

Reports pick content blocks from paragraphs, subheadings, bullet and numbered lists, tables, bar/line/pie charts, callouts, quotes and (when asked) images. Page count comes from the prompt ("a 30-page PDF"), up to `MAX_REPORT_PAGES` (default 40).

## Brand

`app/brand.py` holds the deck engine's palette: cream `#EDD9C2`, teal `#29D9C2`, coral `#FF523D`, green `#006C5B`, petrol `#00505A`.

- **PDF:** cream cover with the A_ logo, teal hand and sphere; contents page and bookmarks; coral `_` after every title; teal table headers; footer mark.
- **PDF fonts:** Source Sans 3 (SIL OFL, `app/assets/fonts/`) is embedded so Polish characters render.
- **Word:** styles use **Acumin Pro**, which falls back on machines without it, and the table of contents updates when the file is opened.

## Editing

- **DOCX / PPTX:** text is replaced inside runs, so bold, fonts and layout survive. Text that spans several runs is merged into the first run.
- **XLSX:** cell edits plus text replacement.
- **TXT / MD / CSV / JSON:** the file is rewritten in full.
- **PDF:** cannot be edited in place, so it is regenerated as a revised branded report.
- **Earlier output:** "Add a section on risks" or "make this a Word document" revises the last generated report, using the markdown the browser kept. "Turn this into a presentation" sends that report to the deck engine.

## Images

Images are generated only when the user explicitly asks ("with illustrations", "z ilustracjami") **and** `IMAGE_BASE_URL` is set. Otherwise the request is ignored with a warning, and the *Image* chip is hidden. Images are converted to JPEG and capped at `MAX_REPORT_IMAGES` per report to stay under Vercel's response limit. Privacy: prompts go to whichever provider you configure, so use a self-hosted server if they must stay on your hardware.

## Deck engine links

The gateway signs short-lived links with `DECK_ENGINE_TOKEN_SECRET`, in the same HMAC format as `engine/app/auth.py`. The browser then downloads the `.pptx` and previews directly from `slides-api`, and large decks never pass through Vercel. Links expire after `DECK_LINK_TTL_SECONDS`, and reopening the chat signs fresh ones.

## Limits on Vercel

- Requests and responses are limited to about 4.5 MB. Edited uploads are capped by `MAX_CREATE_UPLOAD_BYTES` (4 MB), and reports with images stay well below the limit.
- The default duration with Fluid compute is 300 s. A 40-page report takes about 2 to 3 minutes on the RTX 5090 with `SECTION_CONCURRENCY=6`.
