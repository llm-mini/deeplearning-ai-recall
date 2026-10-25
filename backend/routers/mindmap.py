"""
Mind map endpoints: an explorable, incremental learning surface over a course's
stored transcripts. Shared per catalog course (like transcripts).

  GET    /api/mindmap/{catalog_id}            -> {status, error, nodes}
  POST   /api/mindmap/{catalog_id}/seed       -> start background seed (map-reduce)
  POST   /api/mindmap/node/{node_id}/expand   -> synchronous: grow a node
  POST   /api/mindmap/node/{node_id}/ask      -> synchronous: attach a Q&A branch
  DELETE /api/mindmap/{catalog_id}            -> reset the map
"""

import json
import re
import sqlite3
import threading

from fastapi import APIRouter, Depends, HTTPException

from core.database import get_connection, get_db
from routers.auth import get_current_user
from services import llm

router = APIRouter(prefix="/api/mindmap", tags=["MindMap"])

_WORD_RE = re.compile(r"[a-zA-Z0-9]{3,}")
_STOP = {"the", "and", "for", "with", "that", "this", "how", "what", "why", "are", "from", "into"}


def _run_bg_sync(fn):
    """Background thread for the seed job. Unlike scrapes, LLM/httpx calls are
    synchronous and need no Playwright subprocess, so a plain thread suffices."""
    threading.Thread(target=fn, daemon=True).start()


# --------------------------------------------------------------- serialization

def _node_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "parent_id": row["parent_id"],
        "node_type": row["node_type"],
        "title": row["title"],
        "summary": row["summary"],
        "source": row["source"],
        "sources": json.loads(row["sources_json"]) if row["sources_json"] else [],
        "expanded": bool(row["expanded"]),
        "position": row["position"],
    }


def _insert_node(conn, catalog_id, parent_id, node_type, title, summary, source, sources, position):
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO mind_map_nodes
             (catalog_id, parent_id, node_type, title, summary, source, sources_json, position)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (catalog_id, parent_id, node_type, title, summary, source,
         json.dumps(sources) if sources else None, position),
    )
    conn.commit()
    cur.execute("SELECT * FROM mind_map_nodes WHERE id = ?", (cur.lastrowid,))
    return _node_dict(cur.fetchone())


def _retrieve_context(conn, catalog_id, query, limit=3) -> str:
    """Pick the top transcripts for a query by term overlap; prefer cached summaries.
    Never returns the whole course -- keeps calls inside free-tier context limits."""
    terms = {w.lower() for w in _WORD_RE.findall(query or "")} - _STOP
    rows = conn.execute(
        "SELECT lesson_title, summary, transcript_text FROM catalog_transcripts WHERE catalog_id = ?",
        (catalog_id,),
    ).fetchall()
    scored = []
    for r in rows:
        hay = f"{r['lesson_title']} {r['summary'] or ''}".lower()
        score = sum(1 for t in terms if t in hay)
        scored.append((score, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    picked = [r for s, r in scored[:limit]] or [r for _, r in scored[: min(limit, len(scored))]]
    parts = []
    for r in picked:
        body = r["summary"] or (r["transcript_text"] or "")[:4000]
        parts.append(f"### {r['lesson_title']}\n{body}")
    return "\n\n".join(parts)


# ------------------------------------------------------------------- seed job

def _seed_job(catalog_id: int):
    conn = get_connection()
    try:
        cat = conn.execute(
            "SELECT title, syllabus FROM course_catalog WHERE id = ?", (catalog_id,)
        ).fetchone()
        if not cat:
            raise RuntimeError("Course not found")
        syllabus = json.loads(cat["syllabus"]) if cat["syllabus"] else []

        lessons = conn.execute(
            "SELECT id, lesson_title, transcript_text, summary, concepts_json "
            "FROM catalog_transcripts WHERE catalog_id = ? ORDER BY week_num, position",
            (catalog_id,),
        ).fetchall()

        # MAP: summarize any lesson that isn't cached yet.
        summaries = []
        for ls in lessons:
            summary, concepts = ls["summary"], ls["concepts_json"]
            if not summary:
                res = llm.summarize_lesson(ls["lesson_title"], ls["transcript_text"])
                summary = res["summary"]
                concepts = json.dumps(res["concepts"])
                conn.execute(
                    "UPDATE catalog_transcripts SET summary = ?, concepts_json = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (summary, concepts, ls["id"]),
                )
                conn.commit()
            summaries.append({
                "title": ls["lesson_title"],
                "summary": summary,
                "concepts": json.loads(concepts) if concepts else [],
            })

        # REDUCE: root + top concepts from the short summaries only.
        data = llm.generate_seed(cat["title"], syllabus, summaries)
        root = _insert_node(
            conn, catalog_id, None, "root",
            data["root"].get("title") or cat["title"], data["root"].get("summary") or "",
            "transcript", None, 0,
        )
        concept_nodes = []
        for i, child in enumerate(data["children"]):
            concept_nodes.append(_insert_node(
                conn, catalog_id, root["id"], "concept",
                child.get("title"), child.get("summary") or "", "transcript", None, i,
            ))
        conn.execute(
            "UPDATE mind_maps SET total_concepts = ?, built_concepts = 0, "
            "updated_at = CURRENT_TIMESTAMP WHERE catalog_id = ?",
            (len(concept_nodes), catalog_id),
        )
        conn.commit()

        # BUILD: one bounded level of terminal leaves per concept (the tree ends here).
        for concept in concept_nodes:
            context = _retrieve_context(conn, catalog_id, f"{concept['title']} {concept['summary'] or ''}")
            leaves = llm.generate_leaves(concept["title"], concept["summary"] or "", context)
            for j, leaf in enumerate(leaves):
                _insert_node(
                    conn, catalog_id, concept["id"], "leaf",
                    leaf["title"], leaf["summary"], leaf["source"], leaf.get("sources"), j,
                )
            conn.execute(
                "UPDATE mind_maps SET built_concepts = built_concepts + 1, "
                "updated_at = CURRENT_TIMESTAMP WHERE catalog_id = ?",
                (catalog_id,),
            )
            conn.commit()

        conn.execute(
            "UPDATE mind_maps SET status = 'ready', error = NULL, updated_at = CURRENT_TIMESTAMP "
            "WHERE catalog_id = ?",
            (catalog_id,),
        )
        conn.commit()
    except Exception as e:  # noqa: BLE001
        conn.execute(
            "UPDATE mind_maps SET status = 'error', error = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE catalog_id = ?",
            (str(e), catalog_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------- routes

@router.get("/{catalog_id}")
def get_mindmap(catalog_id: int, _user: dict = Depends(get_current_user),
                db: sqlite3.Connection = Depends(get_db)):
    mm = db.execute(
        "SELECT status, error, total_concepts, built_concepts FROM mind_maps WHERE catalog_id = ?",
        (catalog_id,),
    ).fetchone()
    nodes = db.execute(
        "SELECT * FROM mind_map_nodes WHERE catalog_id = ? ORDER BY position, id", (catalog_id,)
    ).fetchall()
    return {
        "status": mm["status"] if mm else "none",
        "error": mm["error"] if mm else None,
        "total_concepts": mm["total_concepts"] if mm else 0,
        "built_concepts": mm["built_concepts"] if mm else 0,
        "nodes": [_node_dict(n) for n in nodes],
    }


@router.post("/{catalog_id}/seed")
def seed_mindmap(catalog_id: int, _user: dict = Depends(get_current_user),
                 db: sqlite3.Connection = Depends(get_db)):
    cat = db.execute("SELECT kind, status FROM course_catalog WHERE id = ?", (catalog_id,)).fetchone()
    if not cat:
        raise HTTPException(status_code=404, detail="Course not found")
    if cat["status"] != "ready":
        raise HTTPException(status_code=400, detail="Course transcripts are not ready yet")
    if cat["kind"] != "short_course":
        raise HTTPException(status_code=400, detail="Mind maps are available for short courses first")

    mm = db.execute("SELECT status FROM mind_maps WHERE catalog_id = ?", (catalog_id,)).fetchone()
    if mm and mm["status"] in ("seeding", "ready"):
        return {"status": mm["status"]}

    # Fresh build (also covers retry after an error): clear any stale nodes.
    db.execute("DELETE FROM mind_map_nodes WHERE catalog_id = ?", (catalog_id,))
    db.execute(
        "INSERT INTO mind_maps (catalog_id, status) VALUES (?, 'seeding') "
        "ON CONFLICT(catalog_id) DO UPDATE SET status = 'seeding', error = NULL, "
        "updated_at = CURRENT_TIMESTAMP",
        (catalog_id,),
    )
    db.commit()
    _run_bg_sync(lambda: _seed_job(catalog_id))
    return {"status": "seeding"}


def _load_node(db, node_id):
    row = db.execute("SELECT * FROM mind_map_nodes WHERE id = ?", (node_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Node not found")
    return row


@router.post("/node/{node_id}/ask")
def ask_node(node_id: int, payload: dict, _user: dict = Depends(get_current_user),
             db: sqlite3.Connection = Depends(get_db)):
    question = (payload or {}).get("question", "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question is required")
    node = _load_node(db, node_id)
    context = _retrieve_context(db, node["catalog_id"], f"{node['title']} {question}")
    ans = llm.answer_question(node["title"], question, context)

    base = db.execute(
        "SELECT COALESCE(MAX(position), -1) AS m FROM mind_map_nodes WHERE parent_id = ?", (node_id,)
    ).fetchone()["m"]
    created = _insert_node(
        db, node["catalog_id"], node_id, "question",
        ans["title"], ans["summary"], ans["source"], ans.get("sources"), base + 1,
    )
    return {"node": created}


@router.delete("/{catalog_id}")
def delete_mindmap(catalog_id: int, _user: dict = Depends(get_current_user),
                   db: sqlite3.Connection = Depends(get_db)):
    db.execute("DELETE FROM mind_map_nodes WHERE catalog_id = ?", (catalog_id,))
    db.execute("DELETE FROM mind_maps WHERE catalog_id = ?", (catalog_id,))
    db.commit()
    return {"status": "success"}
