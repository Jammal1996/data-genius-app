# Deploying DataGenius to Render

Two files to add to your repo root, then 5 clicks in the dashboard.

---

## What you need

- A [Render account](https://render.com) (free tier works for testing)
- Your project pushed to GitHub or GitLab

---

## Step 1 — Add files to your repo

Copy these two files into the **root** of your project (same folder as `app.py`):

```
data_genius_app/
├── app.py
├── requirements.txt
├── Dockerfile          ← replace with the one from this folder
├── render.yaml         ← new file from this folder
└── static/
```

Commit and push:
```bash
git add Dockerfile render.yaml
git commit -m "Add Render deployment config"
git push
```

---

## Step 2 — Create the Blueprint on Render

1. Go to [dashboard.render.com](https://dashboard.render.com)
2. Click **New** → **Blueprint**
3. Connect your GitHub/GitLab repo
4. Render finds `render.yaml` automatically → click **Apply**

That's it — Render builds the Docker image and deploys it.

---

## Step 3 — Set the SECRET_KEY

After the Blueprint is created, you'll see a prompt for the secret env var:

1. Dashboard → **data-genius** service → **Environment**
2. Find `SECRET_KEY` (marked as "needs value")
3. Click the pencil, enter any long random string, save
4. Render automatically redeploys

Generate a good secret key:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## Step 4 — Your app is live

Render gives you a URL like `https://data-genius.onrender.com`.

Every `git push` to your main branch automatically redeploys.

---

## Free tier notes

- Free services **spin down after 15 minutes of inactivity** and take ~30 seconds to wake up on the next request. This is normal for the free tier.
- The **persistent disk** (`static/` mount) requires a paid plan ($7/month). On the free tier, comment out the `disk:` block in `render.yaml` — uploaded files and generated charts won't survive redeploys, but the app will still work.
- To upgrade: Dashboard → data-genius → Settings → Instance Type → **Starter ($7/mo)**

---

## Manual deploy (without Blueprint)

If you prefer to set it up manually instead of using `render.yaml`:

1. Dashboard → **New** → **Web Service**
2. Connect your repo
3. **Environment**: `Docker`
4. **Dockerfile path**: `./Dockerfile`
5. **Start command**: `gunicorn --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120 app:app`
6. **Add env var**: `SECRET_KEY` = your secret, `FLASK_ENV` = `production`
7. Click **Deploy**

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Build fails with `gcc not found` | Already handled in the Dockerfile — check logs for the specific error |
| `Address already in use` | Make sure the CMD uses `$PORT`, not a hardcoded port |
| Charts/uploads disappear after deploy | Add the persistent disk (paid plan) or store files in S3 |
| App returns 502 after deploy | Check logs: Dashboard → data-genius → Logs. Usually a startup crash |
| `ModuleNotFoundError` | A package in `requirements.txt` is missing or has wrong version |
