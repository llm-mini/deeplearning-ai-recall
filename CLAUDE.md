# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

DeepLearning.AI Recall Companion: a multi-user web app to register DeepLearning.AI courses and store/revise their real video transcripts. Two components:

- `backend/` — FastAPI + SQLite. App auth, course registration, and a logged-in **Playwright** scraper that pulls live lesson structure + transcripts (including gated content).
- `frontend/` — React 19 + TypeScript + Vite (CSS Modules). Dashboard UI.

> The old BeautifulSoup scraper and the Chrome extension (which synced transcripts from live tabs) have been **removed**. Transcripts are now fetched server-side by Playwright.

## Commands

### Backend
```bash
cd backend
pip install -r requirements.txt
playwright install chromium          # one-time: install the browser Playwright drives
python save_session.py               # one-time: log into learn.deeplearning.ai, saves storage_state.json
uvicorn main:app --reload --port 8000
```
`init_db()` runs on startup (main.py), creating `db/tracker.db` + tables if absent. No migration framework — **schema changes mean editing `core/database.py` and deleting `db/tracker.db`** (it's recreated on next start).

`save_session.py` opens a visible Chromium; log in and open an unlocked video lesson, then press Enter. It writes `backend/storage_state.json` (auth cookies) which the scraper reuses for every fetch. This file is gitignored — never commit it.

### Frontend
```bash
cd frontend
npm install
npm run dev        # Vite dev server on :5173
npm run build      # tsc -b && vite build -> frontend/dist
npm run lint       # oxlint (config: .oxlintrc.json)
```
No test runner is configured in either component.

## Architecture & cross-component contracts

**App auth is cookie-based** (`backend/routers/auth.py`). If `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` are unset in `backend/.env`, `/api/auth/login` redirects to `/api/auth/mock-login` (as `developer@deeplearning.ai`) — the default local path. The `session_id` cookie holds the user's email signed with `itsdangerous.Signer`; `get_current_user` is the `Depends` guard on protected routes. Frontend sends `credentials: 'include'` on every fetch (`API_BASE_URL` in `frontend/src/App.tsx`).

> This app auth is separate from the **deeplearning.ai login** captured in `storage_state.json`. The latter is a single shared session used by the scraper for all users — it is not per-app-user.

**The fetch unit is a single course, keyed by slug, and is never fetched twice.** This is the central idea of the data model:

- `course_catalog` — one row per course (short course, standalone course, or a specialization member), unique by `slug`. Holds `status` (`scraping`/`ready`/`error`), progress counters (`total_lessons`, `fetched_count`), `syllabus` (JSON), `kind` (`short_course`/`course`), `error`. **No description.**
- `catalog_transcripts` — transcripts for one catalog course: `week_num`, `position`, exact `lesson_title`, `lesson_slug`, `transcript_text`. Unique by `(catalog_id, week_num, lesson_slug)`.
- `specializations` + `specialization_courses` — a specialization is **not** a catalog row; it's an ordered grouping (`course_num` = C index) over member catalog courses. It holds no transcripts of its own.
- `user_courses` — a user's registration, polymorphic: `(item_type ∈ {catalog, specialization}, item_id)`.

`ensure_catalog_course(conn, url, type)` in `routers/courses.py` is the dedup gate for **standalone** courses: find the catalog row by slug → reuse (any status), else insert `scraping` and launch a background scrape. Reuse works across users and re-registration → the UI shows "Already Available" when the row is already `ready`. The INSERT is wrapped in an `IntegrityError` guard (unique `slug`) so a concurrent/double-click registration reuses the winner's row instead of erroring.

> **Specialization members do NOT go through `ensure_catalog_course`.** On learn.deeplearning.ai, every member course's lessons live under the *same* spec URL slug (`/specializations/<spec>/lesson/...`), so `course_slug_from_url` can't tell them apart. Instead each member gets a **synthetic catalog slug `<spec-slug>-c<N>`** (see `run_spec_scrape`). Consequence: a course registered standalone and the same course as a spec member are **not** deduped (different slugs) → fetched twice. Accepted tradeoff; in practice no DLAI course exists both standalone and inside a specialization.

**Scraping runs in a background thread; the UI polls.** `POST /api/register` returns immediately (row created `scraping`). The scrape runs via `_run_bg(coro_factory)` which spins a **new thread with its own `ProactorEventLoop`** (Windows: uvicorn's default `SelectorEventLoop` cannot spawn the Playwright browser subprocess — `NotImplementedError`). Each job opens its **own sqlite connection** via `get_connection()`. `GET /api/courses` reflects live progress; the frontend polls fast (~2.5s) while anything is `scraping`, else 10s, and renders an SVG progress ring. A course can't be opened until `status === 'ready'`.

- Standalone course → `run_course_scrape(catalog_id, url)` → `_scrape_into_catalog` (the reusable body: scrape, upsert transcripts, bump `fetched_count`/`total_lessons`, set `ready`/`error`).
- Specialization → `run_spec_scrape(spec_id, url)`: resolve members, **pass 1** create ALL member catalog rows + `specialization_courses` mappings up front (so member count is known immediately → stable progress), **pass 2** scrape each not-yet-`ready` member sequentially via `_scrape_into_catalog(..., title_override=<real course name>)`.

**The scraper** (`services/transcriber.py`) is a port of `D:\LEARNING2\CourseRevise\transcribe_course_async.py`, driving headless Chromium with the saved session. Two entry points:
- `resolve_specialization(url)` → `{title, members:[{course_num, url, title}]}`. Members come from the **top "Course N" switcher**: non-active members render a `Course <N> - <pct>%` marker (`MEMBER_MARKERS_JS`); the spec entry always lands on Course 1, so the landed page is added as Course 1 **only when Course 1 has no marker** (menu stayed closed) — otherwise Course 1 would be duplicated. Member entry URLs differ only by lesson id but each loads its own course's weeks, so each scrapes normally.
- `scrape_single_course(url, on_total, on_progress, workers)` → enumerate lessons sequentially (canonical order), fetch transcripts concurrently (N worker browser contexts), build `syllabus` + per-lesson transcripts. `kind` from whether the course has weeks.

**Enumeration rules** (`enumerate_course` / `build_syllabus`):
- **Weeks vs Modules**: `collect_week_entries` matches both `Week N` and `Module N` and returns a `label`; module titles render as `"<label> N"`.
- **Quizzes excluded** entirely (`is_quiz_lesson`) — never enumerated, fetched, or shown.
- **Video-only**: `syllabus` and `total_lessons` are built from lessons that actually produced a transcript. Labs/readings (no transcript) never appear in the outline or counts. During fetch the progress denominator shrinks as non-video lessons are discovered (`on_progress(saved, est_total)`).

**`syllabus` is polymorphic** (consumer contract in `App.tsx`, which branches on `typeof syllabus[0]`):
- short course → flat `string[]` of lesson titles.
- course → nested `[{title:"Week N" | "Module N", topics:[{title, lessons:[]}]}]`.
- specialization → built on read in `build_spec_item`: member syllabi concatenated, module titles rewritten to `"<MemberCourseTitle> - Week N"` so the UI's `split(' - ')` tab logic renders course tabs.

**`GET /api/courses` item shapes** (built by `build_catalog_item` / `build_spec_item`):
- catalog: `{item_type:'catalog', kind, id, title, slug, url, status, total_lessons, fetched_count, error, syllabus, transcripts}`.
- specialization: adds `members_total`, `members_ready` (drive a **course-level** ring: `members_ready/members_total`, monotonic) and `current_title`/`current_fetched`/`current_total` (the member being fetched, for a live lesson-level sub-line). `status` is aggregated across members.

**Delete is a per-user unlink + orphan purge** (`DELETE /api/courses/{item_type}/{item_id}`): removes the `user_courses` row, then `_purge_catalog_if_orphan` deletes the catalog (cascades transcripts) only when no user and no specialization still reference it. Deleting a specialization purges the spec then its now-orphaned member catalogs. This lets a user force a fresh re-fetch by removing their last reference.

### Frontend UI (`frontend/src/App.tsx`, single file)
- Sidebar lists courses in **three collapsible sections** (Short Courses / Courses / Specializations), collapsed by default, grouped by `item_type`/`kind`.
- `ProgressRing` (SVG donut) + `progressOf(course)` helper: catalog items show lesson progress; specialization items show **course** progress (`members_ready/members_total`) plus a sub-line for the currently-fetching member.
- A course is only openable when `status === 'ready'` (gated; others show the ring / error).
- Detail view: specialization → **course tabs** (wrapping, numbered) → **module tabs** (wrapping) showing one module at a time; standalone course → module tabs; short course → flat lesson list. Each lesson with a transcript shows a modal. `activeCourseTab`/`activeModuleIdx` are preserved across polls (don't reset on refresh).
- Toast messages auto-dismiss after ~6s; "Already Available" badge when a freshly registered item was already `ready`.

### Diagnostic
`backend/debug_spec.py` is a standalone read-only script (not wired into the app) that dumps a specialization's course-switcher + week-switcher DOM to `debug_spec.json`. Use it when member discovery breaks on a new specialization layout: `cd backend && python debug_spec.py "<spec-url>"`.

### CORS
`main.py` hardcodes localhost (5173/3000/8000) + deeplearning.ai origins. `allow_credentials=True` is required for cookie auth. (The old `chrome-extension://` regex was removed with the extension.)

### Production serving
`main.py` mounts `frontend/dist` at `/` if it exists. In that mode the hardcoded `localhost` redirect URLs in `auth.py` and `API_BASE_URL` in the frontend are dev-only assumptions to revisit.

## Concurrency notes
SQLite runs in WAL mode with a `busy_timeout`; background scrape tasks each open their own connection. Each scrape launches a headless Chromium with N worker contexts, so registering several *new* courses/specializations at once multiplies browser load — fine for a personal/local tool.
