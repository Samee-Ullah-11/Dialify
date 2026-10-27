# Deploying Dialify

Two paths. **Start with Path A (PythonAnywhere)** — it needs zero code changes,
keeps your SQLite database safe, and has Tesseract OCR pre-installed.
Move to Path B (Render) later when you have real users.

---

## Step 0 — Push the code to GitHub (needed for both paths)

1. Go to **github.com** → sign up / sign in.
2. Click **+ (top right) → New repository**.
   - Name: `dialify`
   - Keep it **Private**
   - Do NOT tick "Add a README" (the project already has files).
3. GitHub shows you a page with commands. Ignore them and run these two
   in a terminal inside the project folder (replace `YOURNAME`):

```
git remote add origin https://github.com/YOURNAME/dialify.git
git push -u origin main
```

   A browser window pops up to sign in to GitHub the first time — approve it.

The git repository and first commit are already prepared for you.

---

## Path A — PythonAnywhere (recommended first deploy, free)

1. Go to **www.pythonanywhere.com** → **Pricing & signup → Create a Beginner account** (free).
2. Once logged in, open a **Bash console** (Consoles → Bash) and run
   (replace `YOURNAME` with your GitHub username):

```
git clone https://github.com/YOURNAME/dialify.git
pip install --user -r dialify/requirements.txt
```

   (For a private repo it asks for your GitHub username and a token —
   create one at github.com → Settings → Developer settings →
   Personal access tokens → Fine-grained → give it read access to the repo.)

3. Go to the **Web** tab → **Add a new web app**:
   - Domain: accept the default `YOURUSERNAME.pythonanywhere.com`
   - Framework: **Manual configuration** (NOT "Flask" — that generates its own app)
   - Python version: 3.11 (or newest offered)

4. Still on the Web tab, edit these fields:
   - **Source code**: `/home/YOURUSERNAME/dialify`
   - **WSGI configuration file**: click it, delete everything, paste:

```python
import sys
sys.path.insert(0, "/home/YOURUSERNAME/dialify")
from app import app as application
```

   - Save the file.

5. Click the green **Reload** button on the Web tab.
6. Open `https://YOURUSERNAME.pythonanywhere.com` — Dialify is live, with HTTPS.

**Updating later:** run `cd dialify && git pull` in a Bash console, then hit
Reload on the Web tab.

**Notes**
- Your SQLite database lives at `/home/YOURUSERNAME/dialify/dialify.db` and
  persists — accounts and contacts are kept.
- Tesseract OCR is pre-installed on PythonAnywhere, so scanned PDFs and
  images work out of the box.
- Free tier gives one web app and modest CPU — plenty for testing and
  first users.

---

## Path B — Render + hosted Postgres (when you outgrow Path A)

Render's free web service has an ephemeral disk (SQLite would be wiped on
every deploy), so this path needs the database moved to hosted Postgres first —
ask Claude to adapt `db.py` before doing this.

1. Create a free Postgres database at **neon.tech** or **supabase.com**
   (both free tiers don't expire; Render's own free Postgres expires after
   30 days). Copy its connection string.
2. Go to **render.com** → sign up with your GitHub account.
3. **New → Web Service** → pick the `dialify` repo.
   - Runtime: Python
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app`
   - Instance type: Free
4. Add an environment variable `DATABASE_URL` = the connection string from
   step 1.
5. Deploy. You get `https://dialify.onrender.com` (or similar), and every
   `git push` auto-deploys.

**Note:** Tesseract is not installed on Render's Python runtime; OCR needs a
Docker-based deploy there (ask Claude for the Dockerfile when you switch).

---

## Custom domain (optional, both paths)

Buy a domain (~$10–15/year at Namecheap/GoDaddy), then:
- PythonAnywhere: custom domains need their paid plan ($5/mo).
- Render: free — add the domain in the service's Settings → Custom Domains
  and set the DNS records it shows you.
