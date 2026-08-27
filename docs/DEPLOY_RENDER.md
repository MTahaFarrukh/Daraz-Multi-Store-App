# Deploy on Render (free web service)

Host the Daraz Multi-Store UI on [Render](https://render.com) **free tier** using Docker (includes Chromium for PDF labels).

**Cost: $0** — good for ~2 users. Tradeoffs below.

## What you get on free

- HTTPS URL like `https://daraz-multi-store.onrender.com`
- OAuth + dashboard + label PDF merge
- **No credit card required** for the free web service

## Tradeoffs (read this)

| Free tier behavior | What it means for you |
|--------------------|------------------------|
| Sleeps after ~15 min idle | First visit may take **30–60 seconds** to wake up |
| No persistent disk | **Re-connect store (OAuth)** after each deploy or long restart |
| 512 MB RAM | PDF print may be slow; use **limit 3** labels first |
| 750 hours/month | Enough for 2 users if not running 24/7 constantly |

Tokens live in the container filesystem while it is running. **`DARAZ_TOKEN_KEY` in env** keeps encryption consistent across deploys, but you still OAuth again after a fresh deploy.

---

## Step-by-step deploy

### 1. Push to GitHub

Commit and push this repo to GitHub.

### 2. Create Render service

**Option A — Blueprint (easiest)**

1. [Render Dashboard](https://dashboard.render.com) → **New → Blueprint**
2. Connect your GitHub repo
3. Render reads [`render.yaml`](../render.yaml) (free plan, no disk)

**Option B — Manual**

1. **New → Web Service** → connect repo
2. **Runtime:** Docker
3. **Instance type:** Free
4. **Region:** Singapore

### 3. Environment variables

In Render → your service → **Environment**:

| Key | Value |
|-----|--------|
| `DARAZ_APP_KEY` | your Daraz app key |
| `DARAZ_APP_SECRET` | your app secret |
| `DARAZ_TOKEN_KEY` | generate once (keep forever): |

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

| Key | Value |
|-----|--------|
| `CHROMIUM_PATH` | `/usr/bin/chromium` |
| `DARAZ_API_BASE` | `https://api.daraz.pk/rest` |
| `DARAZ_OAUTH_AUTHORIZE` | `https://api.daraz.pk/oauth/authorize` |

### 4. Deploy once, then set callback URL

1. Click **Deploy** and wait until live
2. Copy your URL, e.g. `https://daraz-multi-store-xxxx.onrender.com`
3. Add env var:

```
DARAZ_REDIRECT_URI=https://daraz-multi-store-xxxx.onrender.com/oauth/callback
```

4. In **Daraz App Console → Callback URL**, paste the **same** URL
5. **Manual Deploy** once more (so the app loads the new redirect URI)

### 5. Use the app

1. Open your Render URL (wait if it was sleeping)
2. **Connect store** → OAuth
3. **Load orders** → **Print labels PDF** (limit **3** first)

Both users bookmark the same Render URL.

---

## After a deploy or restart

If stores show disconnected:

1. Open `/oauth/login` again (or **Connect store** in the UI)
2. Authorize in Daraz
3. Continue as normal

Access tokens still refresh for ~30 days **while the same container is running**.

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Slow first load | Free tier waking up — wait 30–60s |
| OAuth redirect error | `DARAZ_REDIRECT_URI` must match Daraz Console exactly |
| Print fails | Check **Logs**; try limit 3; Chromium needs RAM on free tier |
| Store gone after deploy | Normal on free — OAuth again |

---

## Upgrade later (optional)

If you want tokens to survive deploys without re-OAuth: switch to **Starter** plan + add a **1 GB disk** at `/var/data` and set `DARAZ_DATA_DIR=/var/data`. See paid notes in git history or ask for a Starter `render.yaml`.

## Custom domain (optional)

Render → **Settings → Custom Domains** → update `DARAZ_REDIRECT_URI` and Daraz callback to match.
