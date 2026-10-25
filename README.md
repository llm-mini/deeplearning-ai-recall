# DeepLearning.AI Recall Companion

A multi-user web app to register DeepLearning.AI courses and revise their real video transcripts. Transcripts are fetched server-side by a logged-in **Playwright** browser session, so gated/paid content is captured too.

## Project Structure

```text
├── backend/
│   ├── core/               # config + SQLite schema
│   ├── routers/            # auth + courses (registration, progress, dedup)
│   ├── services/           # transcriber.py — Playwright scraper
│   ├── save_session.py     # one-time: capture deeplearning.ai login
│   ├── main.py             # FastAPI app
│   └── requirements.txt
└── frontend/
    └── src/                # React 19 + TS + CSS Modules dashboard
```

## Setup

### 1. Backend

```bash
cd backend
pip install -r requirements.txt
playwright install chromium        # install the browser Playwright drives

# One-time: capture a logged-in deeplearning.ai session.
# A visible Chromium opens — log in, open an UNLOCKED video lesson, then press Enter.
python save_session.py             # writes storage_state.json (gitignored)

uvicorn main:app --reload --port 8000
```

If no Google credentials are set in `backend/.env`, the app auto-uses **Developer Mock Login**.

### 2. Frontend

```bash
cd frontend
npm install
npm run dev                        # http://localhost:5173
```

## Usage

1. Open **http://localhost:5173** and click **Continue with Google** (mock login locally).
2. Paste a DeepLearning.AI course URL, pick its type (Short Course / Course / Specialization), and **Register**.
3. The course appears with a **progress ring** while transcripts are fetched in the background; it becomes clickable once complete.
4. If the course was already fetched (by you or anyone), it loads instantly as **"Already Available"** — no re-fetch.
5. Open a course to browse its outline and read captured transcripts. Courses are grouped in the sidebar into collapsible **Short Courses / Courses / Specializations** sections.

## How it works

- A **course is the fetch unit**, keyed by its URL slug, stored once in a shared `course_catalog` and reused across users and across specialization membership — so nothing is ever scraped twice.
- A **specialization** is an ordered grouping over its member courses; registering it reuses any member already in the catalog.
- Registration returns immediately and scraping runs as a background task; the UI polls for live progress.
