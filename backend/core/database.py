import os
import sqlite3
from .config import DB_PATH

def _configure(conn):
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")  # wait out concurrent bg writers
    return conn

def get_db():
    conn = _configure(sqlite3.connect(DB_PATH, check_same_thread=False))
    try:
        yield conn
    finally:
        conn.close()

def get_connection():
    """Standalone connection for background tasks (outside the request lifecycle)."""
    return _configure(sqlite3.connect(DB_PATH, check_same_thread=False))

def init_db():
    os.makedirs("db", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode = WAL")  # concurrent reads during bg writes
    cursor = conn.cursor()

    # Users
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT,
            picture TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Global per-course catalog: the fetch unit, keyed by slug. Shared across users.
    # A single course = short_course, standalone course, or a specialization member.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS course_catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            url TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'short_course',   -- 'short_course' | 'course'
            status TEXT NOT NULL DEFAULT 'scraping',      -- 'scraping' | 'ready' | 'error'
            total_lessons INTEGER NOT NULL DEFAULT 0,
            fetched_count INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            syllabus TEXT,                                -- JSON string
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Transcripts for a single catalog course.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS catalog_transcripts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            catalog_id INTEGER NOT NULL,
            week_num INTEGER NOT NULL DEFAULT 0,
            position INTEGER NOT NULL DEFAULT 0,
            lesson_title TEXT NOT NULL,
            lesson_slug TEXT NOT NULL,
            transcript_text TEXT NOT NULL,
            summary TEXT,                                 -- cached per-lesson LLM summary (mind map map-step)
            concepts_json TEXT,                           -- cached JSON list of key concepts
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(catalog_id, week_num, lesson_slug),
            FOREIGN KEY (catalog_id) REFERENCES course_catalog (id) ON DELETE CASCADE
        )
    """)

    # One mind map per catalog course (shared across users, like transcripts).
    # Gates seed generation and tracks the background map-reduce job.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mind_maps (
            catalog_id INTEGER PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'none',           -- none | seeding | ready | error
            error TEXT,
            total_concepts INTEGER NOT NULL DEFAULT 0,     -- build progress denominator
            built_concepts INTEGER NOT NULL DEFAULT 0,     -- concepts whose leaves are done
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (catalog_id) REFERENCES course_catalog (id) ON DELETE CASCADE
        )
    """)

    # Tree of mind map nodes (parent_id adjacency). Root has parent_id NULL.
    # Concepts/sections and Q&A branches are all nodes; node_type distinguishes them.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mind_map_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            catalog_id INTEGER NOT NULL,
            parent_id INTEGER,                             -- NULL for root
            node_type TEXT NOT NULL DEFAULT 'concept',     -- root | concept | question
            title TEXT NOT NULL,
            summary TEXT,                                  -- markdown shown in the detail panel
            source TEXT NOT NULL DEFAULT 'transcript',     -- transcript | web | llm
            sources_json TEXT,                             -- JSON list of citations [{type, url|ref}]
            expanded INTEGER NOT NULL DEFAULT 0,           -- children already generated?
            position INTEGER NOT NULL DEFAULT 0,           -- order among siblings
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (catalog_id) REFERENCES course_catalog (id) ON DELETE CASCADE,
            FOREIGN KEY (parent_id) REFERENCES mind_map_nodes (id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_mmnodes_catalog ON mind_map_nodes(catalog_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_mmnodes_parent ON mind_map_nodes(parent_id)")

    # Lightweight additive migration: add the mind-map summary cache columns to an
    # EXISTING catalog_transcripts (CREATE TABLE IF NOT EXISTS won't alter it), so
    # pre-existing databases don't need a wipe to gain the mind map feature.
    existing = {r[1] for r in cursor.execute("PRAGMA table_info(catalog_transcripts)").fetchall()}
    for col in ("summary", "concepts_json"):
        if col not in existing:
            cursor.execute(f"ALTER TABLE catalog_transcripts ADD COLUMN {col} TEXT")

    mm_cols = {r[1] for r in cursor.execute("PRAGMA table_info(mind_maps)").fetchall()}
    for col in ("total_concepts", "built_concepts"):
        if col not in mm_cols:
            cursor.execute(f"ALTER TABLE mind_maps ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")

    # A registered specialization (container over member courses).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS specializations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            url TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'scraping',      -- 'scraping' (resolving members) | 'ready' | 'error'
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Ordered membership: specialization -> catalog courses.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS specialization_courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            specialization_id INTEGER NOT NULL,
            catalog_id INTEGER NOT NULL,
            course_num INTEGER NOT NULL DEFAULT 1,        -- C index within the specialization
            UNIQUE(specialization_id, catalog_id),
            FOREIGN KEY (specialization_id) REFERENCES specializations (id) ON DELETE CASCADE,
            FOREIGN KEY (catalog_id) REFERENCES course_catalog (id) ON DELETE CASCADE
        )
    """)

    # A user's registration: polymorphic link to a catalog course or a specialization.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            item_type TEXT NOT NULL,                      -- 'catalog' | 'specialization'
            item_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, item_type, item_id),
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """)

    conn.commit()
    conn.close()
