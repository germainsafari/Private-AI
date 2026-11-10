# Named tunnel `languagetool-home` — status

## Done on this PC

- Docker **languagetool** → `http://127.0.0.1:8081` (healthy)
- **cloudflared** Windows service: **Running**, **Automatic** (MSI: `C:\Program Files (x86)\cloudflared\cloudflared.exe`)
- Tunnel ID: `527c1c4e-a567-4fb8-933c-20f466229e6a`
- Prod quick tunnel (`berry-wow-reflected-templates.trycloudflare.com`) — keep until stable URL works

## You must do in Cloudflare (one screen)

1. [Zero Trust → Networks → Tunnels → `languagetool-home`](https://one.dash.cloudflare.com/)
2. Confirm connector status **Healthy**
3. **Public Hostname** → **Add a public hostname** (Published application)
4. Set:
   - **Subdomain:** `lt` (or `languagetool`)
   - **Domain:** `admindagency.com`
   - **Service:** `http` → `localhost:8081`
5. Save

Stable URL: **`https://lt.admindagency.com`**

## Verify

```powershell
.\scripts\test-languagetool.ps1 -PublicUrl "https://lt.admindagency.com"
```

## Vercel (after verify)

```env
LANGUAGETOOL_URL=https://lt.admindagency.com
LANGUAGETOOL_DEFAULT_LANGUAGE=en-US
```

Redeploy → stop quick tunnel session.

## Security

If the tunnel install token was shared in chat or email, **rotate it** in the tunnel settings (reissue connector token, reinstall service).
