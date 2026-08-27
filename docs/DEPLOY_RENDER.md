# Deploy on Render

Host the Daraz Multi-Store UI on [Render](https://render.com) using Docker (includes Chromium for PDF label merge).

## Prerequisites

- GitHub repo with this project pushed
- Render account
- Daraz Open Platform app ([open.daraz.com](https://open.daraz.com/))
- Fernet key for token encryption (generate once, keep forever):

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Option 1 — Blueprint (recommended)

1. Push this repo to GitHub.
2. In Render: **New → Blueprint** → connect the repo.
3. Render reads [`render.yaml`](../render.yaml) and creates the web service + 1 GB disk.
4. Set secret env vars in the Render dashboard when prompted:
   - `DARAZ_APP_KEY`
   - `DARAZ_APP_SECRET`
   - `DARAZ_TOKEN_KEY` (from command above)
5. After first deploy, copy your Render URL, e.g. `https://daraz-multi-store.onrender.com`.
6. Set **`DARAZ_REDIRECT_URI`** in Render env:

   ```
   https://daraz-multi-store.onrender.com/oauth/callback
   ```

   (Use your actual service URL.)

7. In **Daraz App Console → Callback URL**, set the **same** URL.
8. Redeploy if needed, then open your Render URL and connect a store.

## Option 2 — Manual web service

1. **New → Web Service** → connect GitHub repo.
2. **Runtime:** Docker
3. **Region:** Singapore (closest to Pakistan)
4. Add **Persistent Disk**: mount `/var/data`, 1 GB (Starter plan or higher).
5. Environment variables:

| Key | Value |
|-----|--------|
| `DARAZ_DATA_DIR` | `/var/data` |
| `CHROMIUM_PATH` | `/usr/bin/chromium` |
| `DARAZ_APP_KEY` | your app key |
| `DARAZ_APP_SECRET` | your app secret |
| `DARAZ_REDIRECT_URI` | `https://<your-service>.onrender.com/oauth/callback` |
| `DARAZ_TOKEN_KEY` | Fernet key (do not rotate after stores are connected) |
| `DARAZ_API_BASE` | `https://api.daraz.pk/rest` |
| `DARAZ_OAUTH_AUTHORIZE` | `https://api.daraz.pk/oauth/authorize` |

6. Deploy.

## Free tier notes

- **Free web services spin down** after inactivity (cold start ~30–60s).
- **Persistent disk requires a paid plan** on Render. Without disk, tokens/labels are lost on redeploy — use `DARAZ_TOKEN_KEY` in env but you must re-OAuth after each deploy.
- For production, use **Starter** plan + disk so `data/tokens.json` survives restarts.

## Verify deployment

1. Open `https://<your-service>.onrender.com/`
2. **Connect store** → OAuth
3. **Load orders**
4. **Print labels PDF** (start with limit 3)

## Troubleshooting

| Issue | Fix |
|-------|-----|
| OAuth redirect mismatch | `DARAZ_REDIRECT_URI` must match Daraz Console exactly |
| Print fails / no PDF | Check logs for Chromium; confirm `CHROMIUM_PATH=/usr/bin/chromium` |
| Stores disappear after deploy | Add persistent disk at `/var/data` or re-run OAuth |
| 502 on cold start | Wait for free tier to wake up, or upgrade plan |

## Custom domain (optional)

Render dashboard → your service → **Settings → Custom Domains** → add domain → update `DARAZ_REDIRECT_URI` and Daraz callback to `https://labels.yourdomain.com/oauth/callback`.
