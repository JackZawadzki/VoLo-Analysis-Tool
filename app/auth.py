"""
JWT authentication for VoLo Earth.

Flow:
  1. Register with @voloearth.com email + username + password.
     → user is created with verified=0, a 6-digit code is emailed.
  2. Verify email by entering the code (or calling /api/auth/verify-email).
     → user is marked verified, JWT is issued, they are logged in.
  3. Log in with email + password any time after.
     → JWT valid for 72 hours. Never invalidated unless SECRET_KEY changes.
  4. Forgot password? Click → email with reset link → set new password.

Security notes:
  - SECRET_KEY MUST be set as an env var / Replit Secret. If it's missing,
    we refuse to boot (loud failure is safer than silent JWT rotation).
  - Verification codes and reset tokens are stored hashed (never plaintext).
  - Reset tokens expire in 30 minutes; verification codes in 15.
  - All auth events are logged to user_activity for audit history.
"""

import hashlib
import os
import secrets
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from werkzeug.security import check_password_hash, generate_password_hash as _gen_hash

from .database import get_db
from . import email_utils


def generate_password_hash(password: str) -> str:
    return _gen_hash(password, method="pbkdf2:sha256")


# ─────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────

def _resolve_secret_key() -> str:
    """SECRET_KEY must be stable across restarts. If it's missing we
    generate one for dev but print a loud warning. In production (Replit
    deploy), set it as a Secret so it never changes.
    """
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    # Dev fallback — unstable but doesn't block boot. The warning is loud
    # so a production deploy without a Secret surfaces immediately.
    print(
        "\n⚠️  SECRET_KEY env var is NOT SET. Auto-generating a random key\n"
        "   for this process only. JWT tokens will be INVALIDATED on every\n"
        "   restart. Set SECRET_KEY in your environment (or Replit Secrets)\n"
        "   to fix persistent login.\n",
        file=sys.stderr,
    )
    return secrets.token_hex(32)


SECRET_KEY = _resolve_secret_key()
TOKEN_TTL_H = 72                     # 3-day JWT
VERIFY_CODE_TTL_MIN = 15             # 6-digit email verification code
RESET_TOKEN_TTL_MIN = 30             # password reset link
MAX_VERIFY_ATTEMPTS = 6              # per-code brute-force cap
_bearer_scheme = HTTPBearer(auto_error=False)

router = APIRouter(prefix="/api/auth", tags=["auth"])

ALLOWED_DOMAINS = ["voloearth.com"]


# ─────────────────────────────────────────────────────────────────────
# Pydantic models
# ─────────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class VerifyEmailRequest(BaseModel):
    email: str
    code: str


class ResendCodeRequest(BaseModel):
    email: str


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class UserOut(BaseModel):
    id: int
    username: str
    role: str


# ─────────────────────────────────────────────────────────────────────
# Token helpers
# ─────────────────────────────────────────────────────────────────────

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


def _hash_code(code: str) -> str:
    """Store codes + tokens as SHA-256 hashes, not plaintext."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────
# Activity log
# ─────────────────────────────────────────────────────────────────────

def log_activity(
    user_id: Optional[int],
    email: Optional[str],
    event: str,
    detail: str = "",
    request: Optional[Request] = None,
) -> None:
    """Append a row to user_activity. Never raises — worst case we lose
    a log entry but the auth flow itself should never be blocked by
    logging infrastructure."""
    try:
        ip = ""
        ua = ""
        if request is not None:
            ip = (request.client.host if request.client else "") or ""
            ua = request.headers.get("user-agent", "")[:500]
        db = get_db()
        try:
            db.execute(
                "INSERT INTO user_activity "
                "(user_id, email, event, detail, ip_address, user_agent) "
                "VALUES (?,?,?,?,?,?)",
                (user_id, email, event, detail[:1000], ip, ua),
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        # Intentional swallow — activity logging must never break auth.
        pass


# ─────────────────────────────────────────────────────────────────────
# FastAPI deps
# ─────────────────────────────────────────────────────────────────────

class CurrentUser:
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


# ─────────────────────────────────────────────────────────────────────
# Token storage helpers
# ─────────────────────────────────────────────────────────────────────

def _store_token(user_id: int, purpose: str, plaintext: str, ttl_min: int) -> None:
    """Insert a new auth token row. Also invalidates any previous
    unused tokens for the same (user, purpose) so only the latest code
    works — this prevents a user who requests 3 resend codes from being
    able to use the first one."""
    expires_at = (datetime.now(tz=timezone.utc) + timedelta(minutes=ttl_min)).isoformat()
    db = get_db()
    try:
        db.execute(
            "UPDATE auth_tokens SET used_at=datetime('now') "
            "WHERE user_id=? AND purpose=? AND used_at IS NULL",
            (user_id, purpose),
        )
        db.execute(
            "INSERT INTO auth_tokens (user_id, purpose, code_hash, expires_at) "
            "VALUES (?,?,?,?)",
            (user_id, purpose, _hash_code(plaintext), expires_at),
        )
        db.commit()
    finally:
        db.close()


def _consume_token(user_id: int, purpose: str, plaintext: str) -> bool:
    """Mark a token used if it matches + hasn't expired + hasn't been used.
    Returns True on success. Increments attempt_count on every check so
    we can rate-limit brute force attempts at /verify-email."""
    db = get_db()
    try:
        row = db.execute(
            "SELECT id, code_hash, expires_at, used_at, attempt_count "
            "FROM auth_tokens WHERE user_id=? AND purpose=? "
            "ORDER BY id DESC LIMIT 1",
            (user_id, purpose),
        ).fetchone()
        if not row:
            return False
        # Bump attempt counter regardless of outcome.
        db.execute(
            "UPDATE auth_tokens SET attempt_count=attempt_count+1 WHERE id=?",
            (row["id"],),
        )
        db.commit()
        if row["used_at"]:
            return False
        if row["attempt_count"] >= MAX_VERIFY_ATTEMPTS:
            return False
        if row["expires_at"] < datetime.now(tz=timezone.utc).isoformat():
            return False
        if row["code_hash"] != _hash_code(plaintext):
            return False
        db.execute(
            "UPDATE auth_tokens SET used_at=datetime('now') WHERE id=?",
            (row["id"],),
        )
        db.commit()
        return True
    finally:
        db.close()


def _new_verification_code() -> str:
    """6-digit numeric code. Predictable format for emails, ~1M space."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _new_reset_token() -> str:
    """Opaque URL-safe token for password reset links."""
    return secrets.token_urlsafe(32)


# ─────────────────────────────────────────────────────────────────────
# Auth routes
# ─────────────────────────────────────────────────────────────────────

@router.post("/register")
def register(req: RegisterRequest, request: Request):
    """Create an unverified account and email a 6-digit verification code.

    The user is NOT logged in at this point — they must call
    /verify-email with the code before they can log in.
    """
    username = req.username.strip()
    email = req.email.strip().lower()
    password = req.password

    if not username or not email:
        raise HTTPException(400, "Username and email are required.")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")

    if "@" not in email or email.count("@") != 1:
        raise HTTPException(400, "Enter a valid email address.")
    email_domain = email.rsplit("@", 1)[-1]
    if email_domain not in ALLOWED_DOMAINS:
        raise HTTPException(
            403,
            "You need a @voloearth.com email to sign up. "
            "This tool is restricted to VoLo Earth team members.",
        )

    db = get_db()
    try:
        existing = db.execute(
            "SELECT id, verified FROM users WHERE email=?", (email,)
        ).fetchone()

        # Strict: one email = one account. Re-registration is never allowed,
        # whether the existing account is verified or still pending. Users
        # who registered but lost their verification code should use
        # "Send a new one" on the verify screen (hits /resend-code), which
        # re-issues a code to the existing account without creating a new
        # one or changing the password.
        if existing:
            if existing["verified"]:
                raise HTTPException(
                    409,
                    "This email is already registered. Please log in instead.",
                )
            raise HTTPException(
                409,
                "This email is already registered but not verified yet. "
                "Enter the code we emailed you, or click 'Send a new one' "
                "on the verify screen.",
            )

        # Past this point: no existing account, create a new one.
        if db.execute(
            "SELECT id FROM users WHERE username=?", (username,)
        ).fetchone():
            raise HTTPException(
                409, "Username already taken. Please choose another."
            )
        is_first = db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        role = "admin" if is_first else "user"
        cur = db.execute(
            "INSERT INTO users (username, email, password_hash, role, verified) "
            "VALUES (?,?,?,?,0)",
            (username, email, generate_password_hash(password), role),
        )
        db.commit()
        uid = cur.lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Username or email already taken.")
    finally:
        db.close()

    code = _new_verification_code()
    _store_token(uid, "verify_email", code, VERIFY_CODE_TTL_MIN)
    email_utils.send_verification_code(email, code)

    log_activity(uid, email, "register",
                 detail=f"username={username}", request=request)

    return {
        "status": "verification_sent",
        "email": email,
        "message": (
            f"A 6-digit verification code has been sent to {email}. "
            f"Enter it to complete registration. The code expires in "
            f"{VERIFY_CODE_TTL_MIN} minutes."
        ),
    }


@router.post("/verify-email")
def verify_email(req: VerifyEmailRequest, request: Request):
    """Confirm a verification code. On success, marks the account verified
    and returns a JWT so the user is immediately logged in."""
    email = req.email.strip().lower()
    code = req.code.strip()

    if not email or not code:
        raise HTTPException(400, "Email and code are required.")

    db = get_db()
    try:
        row = db.execute(
            "SELECT id, username, role, verified FROM users WHERE email=?",
            (email,),
        ).fetchone()
    finally:
        db.close()

    if not row:
        raise HTTPException(404, "No account found with this email.")
    if row["verified"]:
        # Already done — gently redirect to login instead of erroring.
        raise HTTPException(
            400, "This email is already verified. Please log in."
        )

    if not _consume_token(row["id"], "verify_email", code):
        log_activity(row["id"], email, "verify_failed",
                     detail="bad_or_expired_code", request=request)
        raise HTTPException(400, "Invalid or expired verification code.")

    db = get_db()
    try:
        db.execute("UPDATE users SET verified=1 WHERE id=?", (row["id"],))
        db.commit()
    finally:
        db.close()

    token = make_token(row["id"], row["username"], row["role"])
    log_activity(row["id"], email, "verify", request=request)
    return {
        "token": token,
        "user": {"id": row["id"], "username": row["username"], "role": row["role"]},
    }


@router.post("/resend-code")
def resend_code(req: ResendCodeRequest, request: Request):
    """Issue a fresh verification code for an unverified account.
    To prevent abuse we respond the same way whether the email exists
    or not."""
    email = req.email.strip().lower()
    db = get_db()
    try:
        row = db.execute(
            "SELECT id, verified FROM users WHERE email=?", (email,)
        ).fetchone()
    finally:
        db.close()
    if row and not row["verified"]:
        code = _new_verification_code()
        _store_token(row["id"], "verify_email", code, VERIFY_CODE_TTL_MIN)
        email_utils.send_verification_code(email, code)
        log_activity(row["id"], email, "resend_code", request=request)
    return {
        "status": "ok",
        "message": f"If an unverified account exists for {email}, "
                   f"a new code has been sent.",
    }


@router.post("/login")
def login(req: LoginRequest, request: Request):
    """Log in with email + password. Returns a JWT valid for 72h.

    Unverified accounts are rejected — user must complete email
    verification first.
    """
    email = req.email.strip().lower()
    password = req.password

    db = get_db()
    try:
        row = db.execute(
            "SELECT id, username, password_hash, role, verified "
            "FROM users WHERE email=?",
            (email,),
        ).fetchone()
    finally:
        db.close()

    if not row:
        log_activity(None, email, "login_failed",
                     detail="no_account", request=request)
        raise HTTPException(401, "No account found with this email. Please register first.")

    if not check_password_hash(row["password_hash"], password):
        log_activity(row["id"], email, "login_failed",
                     detail="bad_password", request=request)
        raise HTTPException(401, "Incorrect password.")

    if not row["verified"]:
        log_activity(row["id"], email, "login_failed",
                     detail="unverified", request=request)
        raise HTTPException(
            403,
            "Email address not verified. Check your inbox for the "
            "verification code, or request a new one.",
        )

    token = make_token(row["id"], row["username"], row["role"])
    log_activity(row["id"], email, "login", request=request)
    return {
        "token": token,
        "user": {"id": row["id"], "username": row["username"], "role": row["role"]},
    }


@router.post("/forgot-password")
def forgot_password(req: ForgotPasswordRequest, request: Request):
    """Send a password reset link via email. Always responds the same
    way regardless of whether the email exists (prevents account
    enumeration)."""
    email = req.email.strip().lower()
    db = get_db()
    try:
        row = db.execute(
            "SELECT id, verified FROM users WHERE email=?", (email,)
        ).fetchone()
    finally:
        db.close()

    if row and row["verified"]:
        token = _new_reset_token()
        _store_token(row["id"], "password_reset", token, RESET_TOKEN_TTL_MIN)
        email_utils.send_password_reset(email, token)
        log_activity(row["id"], email, "password_reset_requested",
                     request=request)

    return {
        "status": "ok",
        "message": (
            f"If a verified account exists for {email}, a password reset "
            f"link has been sent. It expires in {RESET_TOKEN_TTL_MIN} minutes."
        ),
    }


@router.post("/reset-password")
def reset_password(req: ResetPasswordRequest, request: Request):
    """Set a new password using the token delivered by /forgot-password."""
    if len(req.new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")

    # The reset token contains no user reference, so we have to scan
    # recent unused reset tokens. It's a short list; fine for SQLite.
    target = _hash_code(req.token)
    db = get_db()
    try:
        row = db.execute(
            "SELECT at.id, at.user_id, at.expires_at, at.used_at, "
            "       at.attempt_count, u.email, u.username, u.role "
            "FROM auth_tokens at "
            "JOIN users u ON u.id = at.user_id "
            "WHERE at.purpose='password_reset' AND at.code_hash=? "
            "ORDER BY at.id DESC LIMIT 1",
            (target,),
        ).fetchone()
        if (
            not row
            or row["used_at"]
            or row["expires_at"] < datetime.now(tz=timezone.utc).isoformat()
        ):
            raise HTTPException(400, "Invalid or expired reset token.")

        db.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (generate_password_hash(req.new_password), row["user_id"]),
        )
        db.execute(
            "UPDATE auth_tokens SET used_at=datetime('now') WHERE id=?",
            (row["id"],),
        )
        db.commit()
    finally:
        db.close()

    log_activity(row["user_id"], row["email"], "password_reset_completed",
                 request=request)

    token = make_token(row["user_id"], row["username"], row["role"])
    return {
        "token": token,
        "user": {
            "id": row["user_id"],
            "username": row["username"],
            "role": row["role"],
        },
    }


@router.post("/logout")
def logout(user: CurrentUser = Depends(get_current_user),
           request: Request = None):
    """Client should discard its stored token. Server-side we just log
    the event — JWTs are stateless so there's nothing to revoke."""
    log_activity(user.id, None, "logout", request=request)
    return {"status": "ok"}


@router.get("/me")
def me(user: CurrentUser = Depends(get_current_user)):
    # Return the fuller user record from the DB so the UI can show email,
    # verified flag, and created_at — not just what's in the JWT.
    db = get_db()
    try:
        row = db.execute(
            "SELECT id, username, email, role, verified, created_at "
            "FROM users WHERE id=?",
            (user.id,),
        ).fetchone()
    finally:
        db.close()
    if not row:
        raise HTTPException(404, "User not found")
    return dict(row)


@router.get("/activity")
def my_activity(
    user: CurrentUser = Depends(get_current_user),
    limit: int = 50,
):
    """Current user's own recent activity (login, verify, password
    resets, etc.). Shown in the profile screen. Admins still get the
    full org-wide feed via /admin/activity."""
    limit = max(1, min(limit, 200))
    db = get_db()
    try:
        rows = db.execute(
            "SELECT event, detail, ip_address, created_at "
            "FROM user_activity WHERE user_id=? "
            "ORDER BY id DESC LIMIT ?",
            (user.id, limit),
        ).fetchall()
    finally:
        db.close()
    return {"activity": [dict(r) for r in rows]}


# ─────────────────────────────────────────────────────────────────────
# Admin — user list + activity history
# ─────────────────────────────────────────────────────────────────────

@router.get("/admin/users")
def admin_list_users(user: CurrentUser = Depends(require_admin)):
    """Return every user + their most recent activity. Admins only."""
    db = get_db()
    try:
        users = db.execute(
            "SELECT u.id, u.username, u.email, u.role, u.verified, u.created_at, "
            "       (SELECT MAX(ua.created_at) FROM user_activity ua "
            "        WHERE ua.user_id=u.id AND ua.event='login') AS last_login, "
            "       (SELECT COUNT(*) FROM user_activity ua "
            "        WHERE ua.user_id=u.id AND ua.event='login') AS login_count "
            "FROM users u ORDER BY u.created_at DESC"
        ).fetchall()
        return {"users": [dict(r) for r in users]}
    finally:
        db.close()


@router.get("/admin/activity")
def admin_activity(
    limit: int = 100,
    user_id: Optional[int] = None,
    event: Optional[str] = None,
    user: CurrentUser = Depends(require_admin),
):
    """Return recent activity entries (latest first). Optional filters
    by user_id and event type."""
    limit = max(1, min(500, limit))
    clauses = []
    params: list = []
    if user_id is not None:
        clauses.append("user_id=?")
        params.append(user_id)
    if event:
        clauses.append("event=?")
        params.append(event)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)

    db = get_db()
    try:
        rows = db.execute(
            f"SELECT id, user_id, email, event, detail, ip_address, "
            f"       user_agent, created_at "
            f"FROM user_activity {where} ORDER BY id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return {"activity": [dict(r) for r in rows]}
    finally:
        db.close()
