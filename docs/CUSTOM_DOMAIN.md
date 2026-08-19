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
| CNAME | `api` | *(자동 생성)* `abcd1234-….cfargotunnel.com` | **Proxied (orange)** |

Notes:

- Cloudflare supports CNAME on the apex (`@`). Prefer that over GitHub A-record IPs.
- **Do not invent** an `api` CNAME by hand. Never use placeholder text like `xxxx-xxxx.cfargotunnel.com`.
- The real `api` CNAME is created by:
  `bash scripts/setup-named-tunnel.sh`
  (runs `cloudflared tunnel route dns dubby-api api.dubbyai.com`).
- Named tunnels **must stay proxied (orange cloud)** so Cloudflare can route
  traffic to your connector. Grey cloud breaks public access.
- Because `api` is proxied, **WAF / Bot Fight / Browser Integrity Check** apply
  to API traffic. Misconfigured rules cause `403 error code: 1010` in curl and
  `Failed to fetch` in browsers (see §4.5).

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

### 4.5 Cloudflare WAF blocks API (`403` / error `1010`)

Symptoms in Dubby UI:

> API 서버(https://api.dubbyai.com)에 연결할 수 없습니다…

Often the API and tunnel are healthy on the PC, but Cloudflare edge returns
**403 `error code: 1010`** (Browser Integrity Check / Bot Fight). Browsers
surface this as `Failed to fetch` because the block response has no CORS
headers.

In [Cloudflare](https://dash.cloudflare.com) → `dubbyai.com`:

1. **Security → Settings** → turn **Browser Integrity Check** **Off** (zone-wide
   or add a skip rule for hostname `api.dubbyai.com`).
2. **Security → Bots** → disable **Bot Fight Mode** for `api`, or add a WAF
   custom rule: `(http.host eq "api.dubbyai.com")` → **Skip** all managed rules.
3. Optional **Configuration Rule**: lower security level for `api.dubbyai.com`.

Verify from a shell (should be `200`, not `1010`):

```bash
curl -sS -D - https://api.dubbyai.com/healthz -o /dev/null
curl -sS -D - -X OPTIONS https://api.dubbyai.com/v1/projects \
  -H "Origin: https://dubbyai.com" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: authorization,content-type" -o /dev/null
```

If curl shows `1010` but the PC tunnel is up, fix WAF — not uvicorn.

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
