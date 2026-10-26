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

# Initialize itsdangerous signer
signer = Signer(SESSION_SECRET)
