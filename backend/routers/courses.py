import asyncio
import json
import sqlite3
import sys
import threading
from fastapi import APIRouter, HTTPException, Depends

from core.database import get_db, get_connection
from routers.auth import get_current_user
from services.transcriber import (
    course_slug_from_url,
    prettify_slug,
    scrape_single_course,
    resolve_specialization,
)

router = APIRouter(prefix="/api", tags=["Courses"])


def _run_bg(coro_factory):
    """Run an async scrape job in its own thread with a fresh event loop.

    Playwright needs to spawn a browser subprocess; on Windows that requires a
    ProactorEventLoop, but uvicorn's loop is a SelectorEventLoop (no subprocess
    support). Isolating each job in its own Proactor loop sidesteps that and
    also keeps long scrapes off the request loop entirely.
    """
    def runner():
        if sys.platform == "win32":
            loop = asyncio.ProactorEventLoop()
        else:
            loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(coro_factory())
        finally:
            loop.close()

    threading.Thread(target=runner, daemon=True).start()


# ----------------------------------------------------------------- background jobs

async def _scrape_into_catalog(conn: sqlite3.Connection, catalog_id: int, url: str, title_override: str = None):
    """Scrape one course URL into an existing catalog row, updating progress live.
    Caller owns the connection (so this can be reused for spec members).
    `title_override` keeps a known-good name (e.g. a spec member's real course
    title) instead of the scraped page title, which for spec members resolves to
    the specialization name."""
    def on_total(n):
        conn.execute("UPDATE course_catalog SET total_lessons = ? WHERE id = ?", (n, catalog_id))
        conn.commit()

    def on_progress(saved, est_total):
        conn.execute(
            "UPDATE course_catalog SET fetched_count = ?, total_lessons = ? WHERE id = ?",
            (saved, est_total, catalog_id),
        )
        conn.commit()

    try:
        data = await scrape_single_course(url, on_total=on_total, on_progress=on_progress)
        for l in data["lessons"]:
            conn.execute(
                """
                INSERT INTO catalog_transcripts
                    (catalog_id, week_num, position, lesson_title, lesson_slug, transcript_text, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(catalog_id, week_num, lesson_slug) DO UPDATE SET
                    transcript_text = excluded.transcript_text,
                    lesson_title    = excluded.lesson_title,
                    position        = excluded.position,
                    updated_at      = CURRENT_TIMESTAMP
                """,
                (catalog_id, l["week_num"], l["position"], l["title"], l["slug"], l["transcript"]),
            )
        conn.execute(
            """
            UPDATE course_catalog
               SET title = ?, kind = ?, syllabus = ?, total_lessons = ?,
                   fetched_count = ?, status = 'ready', error = NULL
             WHERE id = ?
            """,
            (title_override or data["title"], data["kind"], json.dumps(data["syllabus"]),
             data["total_lessons"], data["total_lessons"], catalog_id),
        )
        conn.commit()
    except Exception as e:  # noqa: BLE001
        conn.execute(
            "UPDATE course_catalog SET status = 'error', error = ? WHERE id = ?",
            (str(e), catalog_id),
        )
        conn.commit()


async def run_course_scrape(catalog_id: int, url: str):
    """Background job: scrape one standalone catalog course."""
    conn = get_connection()
    try:
        await _scrape_into_catalog(conn, catalog_id, url)
    finally:
        conn.close()


def ensure_catalog_course(conn: sqlite3.Connection, url: str, requested_type: str, title: str = None) -> dict:
    """Find the catalog course by slug or create it (and launch a scrape).
    Never fetches a course that already exists -- the dedup gate.
    `title` is an optional nicer name known up front (e.g. a specialization
    member's real title); falls back to the prettified slug."""
    slug = course_slug_from_url(url)
    cur = conn.cursor()
    cur.execute("SELECT * FROM course_catalog WHERE slug = ?", (slug,))
    row = cur.fetchone()
    if row:
        return dict(row)

    kind = "course" if requested_type == "course" else "short_course"
    try:
        cur.execute(
            "INSERT INTO course_catalog (title, slug, url, kind, status) VALUES (?, ?, ?, ?, 'scraping')",
            (title or prettify_slug(slug), slug, url, kind),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # Lost a race with a concurrent registration of the same slug -> reuse it.
        conn.rollback()
        cur.execute("SELECT * FROM course_catalog WHERE slug = ?", (slug,))
        return dict(cur.fetchone())

    catalog_id = cur.lastrowid
    _run_bg(lambda: run_course_scrape(catalog_id, url))
    cur.execute("SELECT * FROM course_catalog WHERE id = ?", (catalog_id,))
    return dict(cur.fetchone())


async def run_spec_scrape(spec_id: int, url: str):
    """Background job: resolve a specialization's member courses, then scrape each.

    Member courses share the specialization's URL slug (their only difference is
    the entry-lesson id), so they can't be keyed by `course_slug_from_url`. Each
    member gets a synthetic catalog slug `<spec-slug>-c<N>` so they stay distinct
    and are reused on spec re-registration.
    """
    conn = get_connection()
    spec_slug = course_slug_from_url(url)
    try:
        resolved = await resolve_specialization(url)
        if resolved.get("title"):
            conn.execute("UPDATE specializations SET title = ? WHERE id = ?", (resolved["title"], spec_id))
            conn.commit()

        # Pass 1: create/find ALL member catalog rows + mappings up front, so the
        # specialization's member count is known immediately (stable progress).
        pending = []  # (catalog_id, url, title) to scrape
        for m in resolved["members"]:
            num = m["course_num"]
            member_title = m.get("title") or f"Course {num}"
            member_slug = f"{spec_slug}-c{num}"
            cur = conn.cursor()
            cur.execute("SELECT id, status FROM course_catalog WHERE slug = ?", (member_slug,))
            row = cur.fetchone()
            if row:
                catalog_id = row["id"]
                already_ready = row["status"] == "ready"
                if m.get("title"):  # refresh tab label if we now have a real name
                    conn.execute("UPDATE course_catalog SET title = ? WHERE id = ?", (member_title, catalog_id))
            else:
                cur.execute(
                    "INSERT INTO course_catalog (title, slug, url, kind, status) VALUES (?, ?, ?, 'course', 'scraping')",
                    (member_title, member_slug, m["url"]),
                )
                catalog_id = cur.lastrowid
                already_ready = False

            conn.execute(
                "INSERT OR IGNORE INTO specialization_courses (specialization_id, catalog_id, course_num) VALUES (?, ?, ?)",
                (spec_id, catalog_id, num),
            )
            if not already_ready:
                pending.append((catalog_id, m["url"], m.get("title")))
        conn.commit()

        # Pass 2: scrape each member sequentially (they share one browser session anyway).
        for catalog_id, member_url, member_title in pending:
            await _scrape_into_catalog(conn, catalog_id, member_url, title_override=member_title)

        conn.execute("UPDATE specializations SET status = 'ready', error = NULL WHERE id = ?", (spec_id,))
        conn.commit()
    except Exception as e:  # noqa: BLE001
        conn.execute("UPDATE specializations SET status = 'error', error = ? WHERE id = ?", (str(e), spec_id))
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------- item builders

def build_catalog_item(conn: sqlite3.Connection, catalog_id: int) -> dict:
    cur = conn.cursor()
    cur.execute("SELECT * FROM course_catalog WHERE id = ?", (catalog_id,))
    c = cur.fetchone()
    if not c:
        return None
    c = dict(c)
    syllabus = json.loads(c["syllabus"]) if c["syllabus"] else []
    cur.execute("SELECT lesson_title, transcript_text FROM catalog_transcripts WHERE catalog_id = ?", (catalog_id,))
    transcripts = {t["lesson_title"]: t["transcript_text"] for t in cur.fetchall()}
    return {
        "item_type": "catalog",
        "kind": c["kind"],
        "id": c["id"],
        "title": c["title"],
        "slug": c["slug"],
        "url": c["url"],
        "status": c["status"],
        "total_lessons": c["total_lessons"],
        "fetched_count": c["fetched_count"],
        "error": c["error"],
        "syllabus": syllabus,
        "transcripts": transcripts,
    }


def build_spec_item(conn: sqlite3.Connection, spec: dict) -> dict:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT sc.course_num, cc.*
          FROM specialization_courses sc
          JOIN course_catalog cc ON cc.id = sc.catalog_id
         WHERE sc.specialization_id = ?
         ORDER BY sc.course_num
        """,
        (spec["id"],),
    )
    members = [dict(r) for r in cur.fetchall()]

    total = sum(m["total_lessons"] for m in members)
    fetched = sum(m["fetched_count"] for m in members)
    statuses = [m["status"] for m in members]
    members_total = len(members)
    members_ready = sum(1 for s in statuses if s == "ready")
    # The member currently being fetched, for a live lesson-level progress line.
    current = next((m for m in members if m["status"] == "scraping"), None)

    if spec["status"] == "error" or any(s == "error" for s in statuses):
        status = "error"
    elif spec["status"] == "scraping" or not members or any(s != "ready" for s in statuses):
        status = "scraping"
    else:
        status = "ready"

    syllabus = []
    transcripts = {}
    for m in members:
        msyl = json.loads(m["syllabus"]) if m["syllabus"] else []
        for mod in msyl:
            if isinstance(mod, dict):
                syllabus.append({
                    "title": f'{m["title"]} - {mod["title"]}',
                    "topics": mod.get("topics", []),
                })
            else:
                syllabus.append({
                    "title": m["title"],
                    "topics": [{"title": mod, "lessons": [mod]}],
                })
        cur.execute("SELECT lesson_title, transcript_text FROM catalog_transcripts WHERE catalog_id = ?", (m["id"],))
        for t in cur.fetchall():
            transcripts[t["lesson_title"]] = t["transcript_text"]

    return {
        "item_type": "specialization",
        "kind": "specialization",
        "id": spec["id"],
        "title": spec["title"],
        "slug": spec["slug"],
        "url": spec["url"],
        "status": status,
        "total_lessons": total,
        "fetched_count": fetched,
        "members_total": members_total,
        "members_ready": members_ready,
        "current_title": current["title"] if current else None,
        "current_fetched": current["fetched_count"] if current else 0,
        "current_total": current["total_lessons"] if current else 0,
        "error": spec["error"],
        "syllabus": syllabus,
        "transcripts": transcripts,
    }


# ---------------------------------------------------------------------------- routes

@router.get("/courses")
def get_courses(current_user: dict = Depends(get_current_user), db: sqlite3.Connection = Depends(get_db)):
    cur = db.cursor()
    cur.execute("SELECT * FROM user_courses WHERE user_id = ? ORDER BY created_at DESC", (current_user["id"],))
    regs = cur.fetchall()

    items = []
    for uc in regs:
        if uc["item_type"] == "catalog":
            item = build_catalog_item(db, uc["item_id"])
            if item:
                items.append(item)
        else:
            cur.execute("SELECT * FROM specializations WHERE id = ?", (uc["item_id"],))
            spec = cur.fetchone()
            if spec:
                items.append(build_spec_item(db, dict(spec)))
    return items


@router.post("/register")
async def register_course(
    payload: dict,
    current_user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    url = payload.get("url")
    course_type = payload.get("type", "short_course")
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")
    if "deeplearning.ai" not in url.lower():
        raise HTTPException(status_code=400, detail="Only deeplearning.ai courses are supported")

    cur = db.cursor()

    if course_type == "specialization":
        slug = course_slug_from_url(url)
        cur.execute("SELECT * FROM specializations WHERE slug = ?", (slug,))
        row = cur.fetchone()
        if row:
            spec = dict(row)
        else:
            try:
                cur.execute(
                    "INSERT INTO specializations (title, slug, url, status) VALUES (?, ?, ?, 'scraping')",
                    (prettify_slug(slug), slug, url),
                )
                db.commit()
                spec_id = cur.lastrowid
                _run_bg(lambda: run_spec_scrape(spec_id, url))
                cur.execute("SELECT * FROM specializations WHERE id = ?", (spec_id,))
                spec = dict(cur.fetchone())
            except sqlite3.IntegrityError:
                # Concurrent registration created it first -> reuse, no second scrape.
                db.rollback()
                cur.execute("SELECT * FROM specializations WHERE slug = ?", (slug,))
                spec = dict(cur.fetchone())

        cur.execute(
            "INSERT OR IGNORE INTO user_courses (user_id, item_type, item_id) VALUES (?, 'specialization', ?)",
            (current_user["id"], spec["id"]),
        )
        db.commit()
        item = build_spec_item(db, spec)
    else:
        cat = ensure_catalog_course(db, url, course_type)
        cur.execute(
            "INSERT OR IGNORE INTO user_courses (user_id, item_type, item_id) VALUES (?, 'catalog', ?)",
            (current_user["id"], cat["id"]),
        )
        db.commit()
        item = build_catalog_item(db, cat["id"])

    item["cached"] = item["status"] == "ready"
    return item


def _purge_catalog_if_orphan(conn: sqlite3.Connection, catalog_id: int):
    """Delete a catalog course (and its transcripts, via cascade) only when no
    user registration and no specialization still reference it. Lets a user
    force a fresh re-fetch by removing their last reference, while preserving
    cross-user / cross-specialization reuse."""
    cur = conn.cursor()
    used_by_user = cur.execute(
        "SELECT 1 FROM user_courses WHERE item_type = 'catalog' AND item_id = ? LIMIT 1",
        (catalog_id,),
    ).fetchone()
    in_spec = cur.execute(
        "SELECT 1 FROM specialization_courses WHERE catalog_id = ? LIMIT 1",
        (catalog_id,),
    ).fetchone()
    if not used_by_user and not in_spec:
        cur.execute("DELETE FROM course_catalog WHERE id = ?", (catalog_id,))
        conn.commit()


@router.delete("/courses/{item_type}/{item_id}")
def delete_course(
    item_type: str,
    item_id: int,
    current_user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    if item_type not in ("catalog", "specialization"):
        raise HTTPException(status_code=400, detail="Invalid item type")

    cur = db.cursor()
    cur.execute(
        "DELETE FROM user_courses WHERE user_id = ? AND item_type = ? AND item_id = ?",
        (current_user["id"], item_type, item_id),
    )
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Course not found")
    db.commit()

    if item_type == "catalog":
        _purge_catalog_if_orphan(db, item_id)
    else:
        # Snapshot member catalogs before the specialization (and its membership
        # rows) may be deleted.
        members = [
            r["catalog_id"]
            for r in cur.execute(
                "SELECT catalog_id FROM specialization_courses WHERE specialization_id = ?",
                (item_id,),
            ).fetchall()
        ]
        still_used = cur.execute(
            "SELECT 1 FROM user_courses WHERE item_type = 'specialization' AND item_id = ? LIMIT 1",
            (item_id,),
        ).fetchone()
        if not still_used:
            cur.execute("DELETE FROM specializations WHERE id = ?", (item_id,))  # cascades membership
            db.commit()
            for mcid in members:
                _purge_catalog_if_orphan(db, mcid)

    return {"status": "success", "message": "Course removed from your dashboard"}
