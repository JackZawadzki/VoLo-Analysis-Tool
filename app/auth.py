"""
JWT authentication for the VoLo RVM integration.

Provides FastAPI dependencies for route-level auth, plus
register/login/me endpoints mounted as an APIRouter.

Auth flow:
  1. Register with @voloearth.com email → verification code sent via Gmail
  2. Verify email with 6-digit code
  3. Login with email + password (persistent JWT stored client-side)
"""

import os
import random
import secrets
import smtplib
import sqlite3
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from werkzeug.security import check_password_hash, generate_password_hash as _gen_hash

def generate_password_hash(password: str) -> str:
    return _gen_hash(password, method="pbkdf2:sha256")

from .database import get_db

SECRET_KEY = os.environ.get("SECRET_KEY", secrets.token_hex(32))
TOKEN_TTL_H = 72  # 3-day tokens so logins persist
_bearer_scheme = HTTPBearer(auto_error=False)

router = APIRouter(prefix="/api/auth", tags=["auth"])

ALLOWED_DOMAINS = ["voloearth.com"]


# ── Pydantic models ──────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str

class VerifyRequest(BaseModel):
    email: str
    code: str

class LoginRequest(BaseModel):
    email: str
    password: str

class UserOut(BaseModel):
    id: int
    username: str
    role: str


# ── Token helpers ─────────────────────────────────────────────────────────────

def make_token(user_id: int, username: str, role: str) -> str:
    payload = {
        "sub": str(user_id),
        "user": username,
        "role": role,
        "exp": datetime.now(tz=timezone.utc) + timedelta(hours=TOKEN_TTL_H),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


# ── Email verification ───────────────────────────────────────────────────────

def _generate_code() -> str:
    return str(random.randint(100000, 999999))


def _send_verification_email(to_email: str, code: str) -> bool:
    """Send 6-digit verification code via Gmail SMTP. Returns True on success."""
    gmail_address = os.environ.get("GMAIL_ADDRESS", "")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD", "")

    if not gmail_address or not gmail_password:
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"VoLo Earth — Verification Code: {code}"
    msg["From"] = gmail_address
    msg["To"] = to_email

    html = f"""
    <div style="font-family: Inter, Arial, sans-serif; max-width: 480px; margin: 0 auto; padding: 30px;">
        <div style="background: linear-gradient(135deg, #1a472a, #2d5f3f); border-radius: 12px; padding: 24px; text-align: center; margin-bottom: 24px;">
            <div style="font-size: 2rem;">🌿</div>
            <div style="color: #a8d5b5; font-size: 0.75rem; letter-spacing: 0.15em; text-transform: uppercase; font-weight: 600;">VoLo Earth Ventures</div>
            <div style="color: white; font-size: 1.2rem; font-weight: 700; margin-top: 8px;">Financial Analysis Tool</div>
        </div>
        <p style="color: #1a1a1a; font-size: 0.95rem;">Your verification code is:</p>
        <div style="background: #f4f8f5; border: 2px solid #2d5f3f; border-radius: 10px; padding: 20px; text-align: center; margin: 16px 0;">
            <span style="font-size: 2rem; font-weight: 700; letter-spacing: 0.3em; color: #2d5f3f;">{code}</span>
        </div>
        <p style="color: #555; font-size: 0.85rem;">Enter this code in the app to verify your account.</p>
        <hr style="border: none; border-top: 1px solid #e0e0e0; margin: 20px 0;">
        <p style="color: #999; font-size: 0.75rem; text-align: center;">VoLo Earth Ventures — Internal Tool</p>
    </div>
    """
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_address, gmail_password)
            server.sendmail(gmail_address, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"[Auth] Email send error: {e}")
        return False


# ── FastAPI dependencies ──────────────────────────────────────────────────────

class CurrentUser:
    """Lightweight object attached to requests after auth."""
    __slots__ = ("id", "username", "role")

    def __init__(self, uid: int, username: str, role: str):
        self.id = uid
        self.username = username
        self.role = role


async def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> CurrentUser:
    if creds is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(creds.credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return CurrentUser(
        uid=int(payload["sub"]),
        username=payload["user"],
        role=payload["role"],
    )


async def get_optional_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> Optional[CurrentUser]:
    """Non-blocking auth — returns None if no valid token."""
    if creds is None:
        return None
    payload = decode_token(creds.credentials)
    if payload is None:
        return None
    return CurrentUser(
        uid=int(payload["sub"]),
        username=payload["user"],
        role=payload["role"],
    )


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ── Auth routes ───────────────────────────────────────────────────────────────

@router.post("/register")
def register(req: RegisterRequest):
    username = req.username.strip()
    email = req.email.strip().lower()
    password = req.password

    if not username or not email or len(password) < 8:
        raise HTTPException(400, "Username, email and password (>=8 chars) required.")

    email_domain = email.split("@")[-1] if "@" in email else ""
    if email_domain not in ALLOWED_DOMAINS:
        raise HTTPException(403, "Registration is restricted to @voloearth.com email addresses.")

    code = _generate_code()
    db = get_db()
    try:
        # Check if email already registered and verified
        existing = db.execute(
            "SELECT id, verified FROM users WHERE email=?", (email,)
        ).fetchone()

        if existing:
            if existing["verified"]:
                raise HTTPException(409, "This email is already registered. Please log in.")
            # Unverified — update credentials and resend code
            db.execute(
                "UPDATE users SET username=?, password_hash=?, verification_code=? WHERE email=?",
                (username, generate_password_hash(password), code, email),
            )
            db.commit()
        else:
            # Check username uniqueness
            if db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
                raise HTTPException(409, "Username already taken.")

            is_first = db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
            role = "admin" if is_first else "user"
            db.execute(
                "INSERT INTO users (username, email, password_hash, role, verified, verification_code) "
                "VALUES (?,?,?,?,0,?)",
                (username, email, generate_password_hash(password), role, code),
            )
            db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Username or email already taken.")
    finally:
        db.close()

    sent = _send_verification_email(email, code)
    if not sent:
        # If Gmail not configured, return code for dev/testing
        return {
            "needs_verification": True,
            "message": f"Account created. Email delivery failed (GMAIL secrets not set). Your code: {code}",
        }

    return {
        "needs_verification": True,
        "message": f"Verification code sent to {email}. Check your inbox.",
    }


@router.post("/verify")
def verify_email(req: VerifyRequest):
    email = req.email.strip().lower()
    code = req.code.strip()

    db = get_db()
    try:
        user = db.execute(
            "SELECT id, username, role, verification_code, verified FROM users WHERE email=?",
            (email,),
        ).fetchone()

        if not user:
            raise HTTPException(404, "Account not found.")

        if user["verified"]:
            # Already verified — just issue a token
            token = make_token(user["id"], user["username"], user["role"])
            return {"token": token, "user": {"id": user["id"], "username": user["username"], "role": user["role"]}}

        if user["verification_code"] != code:
            raise HTTPException(400, "Invalid verification code. Please try again.")

        db.execute(
            "UPDATE users SET verified=1, verification_code=NULL WHERE id=?",
            (user["id"],),
        )
        db.commit()

        token = make_token(user["id"], user["username"], user["role"])
        return {"token": token, "user": {"id": user["id"], "username": user["username"], "role": user["role"]}}
    finally:
        db.close()


@router.post("/login")
def login(req: LoginRequest):
    email = req.email.strip().lower()
    password = req.password

    db = get_db()
    try:
        row = db.execute(
            "SELECT id, username, password_hash, role, verified FROM users WHERE email=?",
            (email,),
        ).fetchone()
    finally:
        db.close()

    if not row:
        raise HTTPException(401, "No account found with this email. Please register first.")

    if not check_password_hash(row["password_hash"], password):
        raise HTTPException(401, "Incorrect password.")

    if not row["verified"]:
        raise HTTPException(403, "Email not verified. Please complete verification first.")

    token = make_token(row["id"], row["username"], row["role"])
    return {
        "token": token,
        "user": {"id": row["id"], "username": row["username"], "role": row["role"]},
    }


@router.get("/me")
def me(user: CurrentUser = Depends(get_current_user)):
    return {"id": user.id, "username": user.username, "role": user.role}


@router.post("/promote-admin")
def promote_to_admin(user: CurrentUser = Depends(get_current_user)):
    """Promote the current user to admin. Temporary dev utility."""
    db = get_db()
    try:
        db.execute("UPDATE users SET role='admin' WHERE id=?", (user.id,))
        db.commit()
    finally:
        db.close()
    new_token = make_token(user.id, user.username, "admin")
    return {"token": new_token, "user": {"id": user.id, "username": user.username, "role": "admin"}}
