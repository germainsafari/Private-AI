# Self-hosted LanguageTool (with English n-grams)

Grammar checking via [LanguageTool HTTP API](https://dev.languagetool.org/public-http-api), with [n-gram confusion detection](https://dev.languagetool.org/finding-errors-using-n-gram-data.html) for English.

## Stack

| Piece | Detail |
|-------|--------|
| Docker | `meyay/languagetool:latest`, port **8081** |
| N-grams | English only, persisted at `C:\languagetool-data\ngrams` |
| Public URL | Cloudflare quick tunnel → update when tunnel restarts |

## Vercel / colleague env vars

Copy from `docs/languagetool.env.local.example` or `.env.local` (not committed):

```env
LANGUAGETOOL_URL=https://<your-subdomain>.trycloudflare.com
LANGUAGETOOL_DEFAULT_LANGUAGE=en-US
LANGUAGETOOL_API_KEY=
```

No `MODEL_NAME` — use `POST {LANGUAGETOOL_URL}/v2/check` with `language` and `text`.

## Start after reboot

```powershell
docker start languagetool
& "$env:USERPROFILE\cloudflared\cloudflared.exe" tunnel --url http://127.0.0.1:8081
```

Update `LANGUAGETOOL_URL` in Vercel if the trycloudflare hostname changed.

## Stable URL (named Cloudflare Tunnel)

Quick tunnels (`*.trycloudflare.com`) change when you stop `cloudflared`. For a **fixed hostname** (e.g. `languagetool.yourdomain.com`):

1. Add your domain to Cloudflare (nameservers on Cloudflare).
2. [Zero Trust](https://one.dash.cloudflare.com/) → **Networks** → **Tunnels** → **Create tunnel** → **Cloudflared**.
3. Install connector on Windows (see dashboard command after **Save tunnel**).
4. **Public Hostname**: subdomain + domain → Service **`http://localhost:8081`**.
5. Run the named connector **in addition to** any quick tunnel until you migrate Vercel env vars.

Install `cloudflared` on PATH (once):

```powershell
winget install Cloudflare.cloudflared
# new terminal, then: cloudflared --version
```

Full UI steps: [Create a tunnel (dashboard)](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-remote-tunnel/).

**This machine:** `cloudflared` runs as a Windows service for tunnel `languagetool-home`. Finish DNS in dashboard → see `docs/NAMED_TUNNEL_NEXT_STEP.md`.

## Recreate container (first-time / reset)

```powershell
docker rm -f languagetool
docker run -d --name languagetool --restart unless-stopped `
  -p 8081:8081 `
  -e Java_Xmx=4g `
  -e "download_ngrams_for_langs=en" `
  -v "C:/languagetool-data/ngrams:/ngrams" `
  meyay/languagetool:latest
```

Add `de,es,fr` to `download_ngrams_for_langs` for more languages (large downloads).

## Tests

```powershell
.\scripts\test-languagetool.ps1
```

N-gram smoke test:

```powershell
curl.exe -d "language=en-US" -d "text=Don't forget to put on the breaks." http://127.0.0.1:8081/v2/check
```

Expect a match suggesting **brakes** (`CONFUSION_RULE_BREAKS_BRAKES`).
