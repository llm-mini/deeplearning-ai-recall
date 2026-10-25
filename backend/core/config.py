import os
from itsdangerous import Signer
from dotenv import load_dotenv

load_dotenv()

# Constants
DB_PATH = "db/tracker.db"
COOKIE_NAME = "session_id"
SESSION_SECRET = os.getenv("SESSION_SECRET_KEY", "super-secret-dev-key")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:8000/api/auth/callback")

# ---- Mind map LLM (OpenRouter) ----
# Default is a free, tool-capable model. Override MINDMAP_MODEL in backend/.env.
# Alternates: qwen/qwen-2.5-72b-instruct:free, google/gemini-2.0-flash-exp:free.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE = os.getenv("OPENROUTER_BASE", "https://openrouter.ai/api/v1")
MINDMAP_MODEL = os.getenv("MINDMAP_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
SEARCH_API_KEY = os.getenv("SEARCH_API_KEY", "")  # optional; unused for the DuckDuckGo fallback

# Initialize itsdangerous signer
signer = Signer(SESSION_SECRET)
