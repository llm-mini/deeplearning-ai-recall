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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(catalog_id, week_num, lesson_slug),
            FOREIGN KEY (catalog_id) REFERENCES course_catalog (id) ON DELETE CASCADE
        )
    """)

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
