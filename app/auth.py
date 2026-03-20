"""
JWT authentication for the VoLo RVM integration.

Provides FastAPI dependencies for route-level auth, plus
register/login/me endpoints mounted as an APIRouter.
"""

import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
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
TOKEN_TTL_H = 24
_bearer_scheme = HTTPBearer(auto_error=False)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ── Pydantic models ──────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str

class LoginRequest(BaseModel):
    username: str
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
        raise HTTPException(400, "username, email and password (>=8 chars) required")

    db = get_db()
    try:
        is_first = db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        role = "admin" if is_first else "user"

        cur = db.execute(
            "INSERT INTO users (username, email, password_hash, role) VALUES (?,?,?,?)",
            (username, email, generate_password_hash(password), role),
        )
        db.commit()
        uid = cur.lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Username or email already taken")
    finally:
        db.close()

    token = make_token(uid, username, role)
    return {"token": token, "user": {"id": uid, "username": username, "role": role}}


@router.post("/login")
def login(req: LoginRequest):
    username = req.username.strip()
    password = req.password

    db = get_db()
    try:
        row = db.execute(
            "SELECT id, username, password_hash, role FROM users WHERE username=?",
            (username,),
        ).fetchone()
    finally:
        db.close()

    if not row or not check_password_hash(row["password_hash"], password):
        raise HTTPException(401, "Invalid credentials")

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
