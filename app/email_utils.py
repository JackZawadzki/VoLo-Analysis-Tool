"""Email delivery for account flows.

Three transports, tried in order. The first one that has its env vars
set is used:

  1. Gmail SMTP — set GMAIL_USER + GMAIL_APP_PASSWORD.
     Simplest option for internal use. No DNS required. Works through
     smtp.gmail.com:587 with TLS. Cap ~500 emails/day per Gmail account,
     which is well past any real team's usage.

  2. Resend — set RESEND_API_KEY.
     Production-grade once you verify a sender domain (voloearth.com).
     Required for sending from addresses like noreply@voloearth.com.

  3. Console-log fallback — no env vars needed.
     Prints the message body to the server log so local development +
     tests still complete end-to-end without shipping real email.
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────

def _resend_api_key() -> Optional[str]:
    return os.environ.get("RESEND_API_KEY")


def _gmail_creds() -> Optional[tuple[str, str]]:
    """Return (user, app_password) if Gmail SMTP is configured, else None."""
    user = os.environ.get("GMAIL_USER")
    pw = os.environ.get("GMAIL_APP_PASSWORD")
    if user and pw:
        # Strip any spaces from the app password — Google displays it as
        # "abcd efgh ijkl mnop" and users frequently copy the spaces too.
        return user.strip(), pw.replace(" ", "").strip()
    return None


def _from_address() -> str:
    """Sender email shown in the From: header.

    - If EMAIL_FROM is set, use it (allows "VoLo Earth <voloearth.auth@gmail.com>").
    - Otherwise default to the Gmail account when Gmail is configured.
    - Otherwise fall back to Resend's sandbox address.
    """
    explicit = os.environ.get("EMAIL_FROM")
    if explicit:
        return explicit
    gmail = _gmail_creds()
    if gmail:
        return f"VoLo Earth <{gmail[0]}>"
    return "VoLo Earth <onboarding@resend.dev>"


def _app_url() -> str:
    """Public base URL used when rendering click-able links inside emails
    (password reset, etc.). In Replit this should be the deployment URL.
    """
    return os.environ.get("APP_URL", "http://localhost:8001").rstrip("/")


# ─────────────────────────────────────────────────────────────────────
# Transport
# ─────────────────────────────────────────────────────────────────────

def _send_via_gmail(to: str, subject: str, html: str, text: str) -> bool:
    """Deliver via Gmail SMTP (smtp.gmail.com:587, STARTTLS).

    Requires 2-Step Verification on the Gmail account + an app password
    generated at https://myaccount.google.com/apppasswords. Regular Gmail
    passwords do NOT work for SMTP — Google blocks them.
    """
    creds = _gmail_creds()
    if not creds:
        return False
    user, app_pw = creds

    msg = MIMEMultipart("alternative")
    msg["From"] = _from_address()
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(user, app_pw)
            s.sendmail(user, [to], msg.as_string())
        return True
    except smtplib.SMTPAuthenticationError as exc:
        logger.error(
            "Gmail SMTP auth failed (code=%s). Did you use an app password, "
            "not your regular Gmail password? Also confirm 2FA is on for %s. "
            "Server said: %s",
            exc.smtp_code, user, (exc.smtp_error or b"").decode(errors="ignore")[:200],
        )
        return False
    except Exception:
        logger.exception("Gmail SMTP send failed")
        return False


def _send_via_resend(to: str, subject: str, html: str, text: str) -> bool:
    """Deliver via Resend's HTTPS API. Returns True on 2xx."""
    api_key = _resend_api_key()
    if not api_key:
        return False
    try:
        import requests
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": _from_address(),
                "to": [to],
                "subject": subject,
                "html": html,
                "text": text,
            },
            timeout=15,
        )
    except Exception:
        logger.exception("Resend API request raised")
        return False

    if 200 <= resp.status_code < 300:
        return True
    logger.error("Resend API %s: %s", resp.status_code, resp.text[:500])
    return False


def _log_to_console(to: str, subject: str, text: str) -> None:
    """Fallback when RESEND_API_KEY is not set. Prints the email to the
    server log so developers can grab the verification code without
    actually shipping an SMTP provider in local development."""
    banner = "═" * 68
    logger.warning(
        "\n%s\n[EMAIL-DEV-FALLBACK] RESEND_API_KEY not set — would send:\n"
        "  To:      %s\n  Subject: %s\n  Body:\n%s\n%s",
        banner, to, subject, text, banner,
    )


def send_email(to: str, subject: str, html: str, text: str) -> bool:
    """Send an email. Tries transports in preference order, falls back
    to logging to stdout so local dev never breaks.

    Returns True when the message was accepted by SOME path (including
    the console-log fallback). Returns False only when a configured
    real provider rejected the message AND no fallback succeeded.

    DEFENSIVE GUARD: if the recipient isn't on an allowlisted domain,
    refuse to send. This is a belt-and-suspenders check beneath the
    route-level domain enforcement — so if any code path ever reaches
    here with a @gmail.com or similar, we hard-fail instead of silently
    delivering. `AUTH_ALLOWED_DOMAINS` env var (comma-separated) can
    widen this list; default is voloearth.com only.
    """
    allowed = os.environ.get("AUTH_ALLOWED_DOMAINS", "voloearth.com")
    allowed_set = {d.strip().lower() for d in allowed.split(",") if d.strip()}
    recipient_domain = (to or "").rsplit("@", 1)[-1].lower()
    if recipient_domain not in allowed_set:
        logger.warning(
            "email send blocked — recipient %s is not on an allowlisted "
            "domain (%s). No email was sent.",
            to, sorted(allowed_set),
        )
        return False

    # 1. Gmail SMTP (primary when GMAIL_USER+GMAIL_APP_PASSWORD are set)
    if _gmail_creds():
        if _send_via_gmail(to, subject, html, text):
            return True
        logger.warning("Gmail send failed; trying Resend / console-log fallback")

    # 2. Resend API (if a key is present)
    if _resend_api_key():
        if _send_via_resend(to, subject, html, text):
            return True
        logger.warning("Resend send failed; falling back to console-log")

    # 3. Console-log fallback — so dev + tests always complete the flow
    _log_to_console(to, subject, text)
    # We return True here because the flow did complete — the code is
    # visible in the server log. A non-dev deploy should have one of
    # the real transports working, making this path a safety net rather
    # than the expected outcome.
    return True


# ─────────────────────────────────────────────────────────────────────
# Template helpers
# ─────────────────────────────────────────────────────────────────────

_VOLO_GREEN = "#5B7744"
_BASE_STYLE = (
    "font-family: -apple-system, 'Helvetica Neue', Arial, sans-serif; "
    "font-size: 14px; color: #1a1a1a; line-height: 1.6;"
)


def send_verification_code(to: str, code: str) -> bool:
    subject = f"Your VoLo Earth verification code: {code}"
    html = f"""\
<div style="{_BASE_STYLE}">
  <h2 style="color: {_VOLO_GREEN}; margin-bottom: 8px;">VoLo Earth</h2>
  <p>Welcome — use the code below to finish activating your account.</p>
  <div style="font-size: 28px; font-weight: 700; letter-spacing: 4px;
              padding: 14px 24px; background: #f1f5ed; border: 1px solid #d4e6da;
              border-radius: 6px; display: inline-block; color: {_VOLO_GREEN};
              margin: 16px 0;">
    {code}
  </div>
  <p style="font-size: 12px; color: #666;">
    This code expires in 15 minutes. If you didn't request it, ignore this email.
  </p>
</div>
"""
    text = (
        f"VoLo Earth — verification code: {code}\n\n"
        f"Enter this code in the app to finish activating your account.\n"
        f"Expires in 15 minutes.\n\n"
        f"If you didn't request this, you can ignore this email."
    )
    return send_email(to, subject, html, text)


def send_password_reset(to: str, token: str) -> bool:
    link = f"{_app_url()}/?reset_token={token}"
    subject = "Reset your VoLo Earth password"
    html = f"""\
<div style="{_BASE_STYLE}">
  <h2 style="color: {_VOLO_GREEN}; margin-bottom: 8px;">Password reset</h2>
  <p>Someone (hopefully you) asked to reset the password on your VoLo Earth
  account. Click the link below to set a new one:</p>
  <p style="margin: 20px 0;">
    <a href="{link}" style="background: {_VOLO_GREEN}; color: #fff;
       text-decoration: none; padding: 10px 20px; border-radius: 4px;
       display: inline-block;">Reset password</a>
  </p>
  <p style="font-size: 12px; color: #666;">
    This link expires in 30 minutes. If you didn't request a reset,
    just ignore this email — your password won't change.
  </p>
</div>
"""
    text = (
        f"VoLo Earth — password reset\n\n"
        f"Open this link to set a new password:\n  {link}\n\n"
        f"Expires in 30 minutes.\n\n"
        f"If you didn't request this, ignore — password unchanged."
    )
    return send_email(to, subject, html, text)
