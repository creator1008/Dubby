# Dubby custom domain (without Lightsail yet)

Primary site: `https://dubbyai.com`  
API (PC via Cloudflare Named Tunnel): `https://api.dubbyai.com`  

Lightsail can replace the named tunnel later; DNS for `api` then becomes an A record to the VPS IP.

## 1. Cloudflare DNS records

In [Cloudflare DNS](https://dash.cloudflare.com) → `dubbyai.com` → **Records**:

| Type | Name | Content | Proxy |
| --- | --- | --- | --- |
| CNAME | `@` (or `dubbyai.com`) | `creator1008.github.io` | DNS only (grey) |
| CNAME | `www` | `creator1008.github.io` | DNS only (grey) |
| CNAME | `api` | `<TUNNEL_ID>.cfargotunnel.com` | DNS only (grey) |

Notes:

- Cloudflare supports CNAME on the apex (`@`). Prefer that over GitHub A-record IPs.
- The `api` CNAME is usually created automatically by  
  `cloudflared tunnel route dns dubby-api api.dubbyai.com`.
- Keep **DNS only** (grey cloud) for `api` while using a named tunnel / later Caddy.

You can delete the old Hostinger parking A records (`75.2…`, `99.83…`) once the GitHub CNAMEs are in place.

## 2. GitHub Pages custom domain

1. Repo **Settings → Pages → Custom domain** → `dubbyai.com` → Save.
2. Enable **Enforce HTTPS** after DNS checks pass.
3. This repo already ships `public/CNAME` (`dubbyai.com`) so deploys keep the domain.

## 3. Supabase Auth URLs

Site URL / redirect allow list must include the new origin:

- Site URL: `https://dubbyai.com/`
- Redirects: `https://dubbyai.com/auth/callback/`, `https://dubbyai.com/**`

```bash
set SUPABASE_ACCESS_TOKEN=sbp_...
set DUBBY_SITE_URL=https://dubbyai.com
python scripts/update-supabase-auth-urls.py
```

Or set the same values in the Supabase dashboard → Authentication → URL configuration.

## 4. Named Tunnel for API (PC, until Lightsail)

One-time:

```bash
# Windows Git Bash / from repo root
bash scripts/setup-named-tunnel.sh
```

That script will:

1. `cloudflared tunnel login` (browser)
2. Create tunnel `dubby-api` (if missing)
3. Route DNS `api.dubbyai.com` → the tunnel
4. Write `infra/cloudflared/config.yml`

Every time you develop / serve mobile:

```bash
# Terminal A — API
cd api && .venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# Terminal B — worker
cd api && .venv/Scripts/python.exe -m app.worker.runner

# Terminal C — stable public URL
bash scripts/run-named-tunnel.sh
```

Then set GitHub Actions secret:

- `NEXT_PUBLIC_API_ORIGIN` = `https://api.dubbyai.com`

And local `api/.env`:

```env
CORS_ORIGINS=https://dubbyai.com,https://www.dubbyai.com,https://creator1008.github.io,http://localhost:3000,https://localhost,capacitor://localhost
```

## 5. Verify

```bash
curl -sS https://dubbyai.com/ | head
curl -sS https://api.dubbyai.com/healthz
```

Mobile: open `https://dubbyai.com/app/new/` (no `?api=` needed once the secret is deployed).

## 6. Later: switch API to Lightsail

1. Deploy stack per `infra/DEPLOY.md` with `DUBBY_API_DOMAIN=api.dubbyai.com`.
2. Stop the named tunnel on the PC.
3. Change Cloudflare `api` from tunnel CNAME to **A → Lightsail static IP** (DNS only).
