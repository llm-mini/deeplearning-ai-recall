import sqlite3
from fastapi import APIRouter, Request, Response, HTTPException, Depends
from fastapi.responses import RedirectResponse
import httpx
from itsdangerous import BadSignature

from core.config import COOKIE_NAME, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, REDIRECT_URI, signer
from core.database import get_db

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

# Authentication Dependency
def get_current_user(request: Request, db: sqlite3.Connection = Depends(get_db)):
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        email = signer.unsign(cookie).decode('utf-8')
    except BadSignature:
        raise HTTPException(status_code=401, detail="Invalid session session cookie")
        
    cursor = db.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
    user = cursor.fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return dict(user)

@router.get("/login")
def login():
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return RedirectResponse(url="/api/auth/mock-login")
        
    google_auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?response_type=code"
        f"&client_id={GOOGLE_CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope=openid%20email%20profile"
        f"&prompt=select_account"
    )
    return RedirectResponse(url=google_auth_url)

@router.get("/mock-login")
def mock_login(response: Response, db: sqlite3.Connection = Depends(get_db)):
    email = "developer@deeplearning.ai"
    name = "DL.AI Developer"
    picture = "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=150&auto=format&fit=crop&q=80"
    
    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO users (email, name, picture) VALUES (?, ?, ?) ON CONFLICT(email) DO UPDATE SET name=excluded.name, picture=excluded.picture",
        (email, name, picture)
    )
    db.commit()
    
    signed_val = signer.sign(email.encode('utf-8')).decode('utf-8')
    frontend_url = "http://localhost:5173/"
    res = RedirectResponse(url=frontend_url)
    res.set_cookie(
        key=COOKIE_NAME,
        value=signed_val,
        path="/",
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=3600 * 24 * 7
    )
    return res

@router.get("/callback")
async def callback(code: str, response: Response, db: sqlite3.Connection = Depends(get_db)):
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=400, detail="OAuth credentials not configured")
        
    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code"
    }
    
    async with httpx.AsyncClient() as client:
        token_res = await client.post(token_url, data=data)
        if token_res.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Failed to retrieve Google token: {token_res.text}")
        token_data = token_res.json()
        
    access_token = token_data.get("access_token")
    
    userinfo_url = "https://www.googleapis.com/oauth2/v3/userinfo"
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient() as client:
        user_res = await client.get(userinfo_url, headers=headers)
        if user_res.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to retrieve Google userinfo")
        userinfo = user_res.json()
        
    email = userinfo.get("email")
    name = userinfo.get("name", "")
    picture = userinfo.get("picture", "")
    
    if not email:
        raise HTTPException(status_code=400, detail="Email not provided by Google")
        
    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO users (email, name, picture) VALUES (?, ?, ?) ON CONFLICT(email) DO UPDATE SET name=excluded.name, picture=excluded.picture",
        (email, name, picture)
    )
    db.commit()
    
    signed_val = signer.sign(email.encode('utf-8')).decode('utf-8')
    frontend_url = "http://localhost:5173/"
    res = RedirectResponse(url=frontend_url)
    res.set_cookie(
        key=COOKIE_NAME,
        value=signed_val,
        path="/",
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=3600 * 24 * 7
    )
    return res

@router.get("/me")
def get_me(current_user: dict = Depends(get_current_user)):
    return current_user

@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        samesite="lax",
        secure=False
    )
    return {"status": "success", "message": "Logged out"}
