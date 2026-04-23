# Deployment — VoLo Earth Underwriting Engine

Target: Replit (Reserved VM recommended — persistent disk, doesn't sleep).

## Required Replit Secrets

Open your Repl → click the **🔒 Secrets** tab in the left sidebar → add each key below:

| Key | Example value | What it's for |
|---|---|---|
| `SECRET_KEY` | `3f8a9b2c1d4e5f6789012abcdef3456789abcdef0123456789abcdef01234567` | JWT signing secret. **Must be stable across restarts** or everyone gets logged out on every deploy. Generate with `openssl rand -hex 32`. |
| `ANTHROPIC_API_KEY` | `sk-ant-api03-...` | Claude API access (DDR, banker, memo generator, deck extraction). |
| `GMAIL_USER` | `voloearth.auth@gmail.com` | Gmail account for verification emails. |
| `GMAIL_APP_PASSWORD` | `abcdefghijklmnop` | 16-char Google App Password (requires 2FA on that Gmail account). [Generate here](https://myaccount.google.com/apppasswords). |
| `APP_URL` | `https://your-volo-repl.replit.app` | Used to build password-reset links in emails. |
| `EMAIL_FROM` | `VoLo Earth <voloearth.auth@gmail.com>` | (Optional) Display name + sender shown in recipient inboxes. Defaults to `voloearth.auth@gmail.com`. |

**After adding secrets, click the "Deploy" / "Redeploy" button** so Replit picks them up.

## What happens on boot

1. `pip install -r requirements.txt`
2. `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1`
3. SQLite DB at `data/rvm.db` is auto-created with `_SCHEMA_SQL`
4. Server is ready to accept HTTP

The first person to register becomes admin automatically. Hand that account to Joseph or use it to promote others via `POST /api/auth/promote-admin`.

## Auth flow (what users see)

1. **Register** at `/`: enter @voloearth.com email + password. Backend creates the account with `verified=0` and emails a 6-digit code via Gmail SMTP.
2. **Verify**: enter the 6-digit code on the next screen. Code expires in 15 minutes. Account flips to `verified=1` and the user receives a JWT session (valid 72 hours).
3. **Login**: email + password any time after. Stays logged in across restarts as long as `SECRET_KEY` is stable.
4. **Forgot password**: enter email, receive a reset link (30-min expiry) via Gmail. Click → set new password → logged in.

Only `@voloearth.com` emails can register. Unverified accounts cannot log in.

## Admin endpoints

Any authenticated admin can hit these:
- `GET /api/auth/admin/users` — full user list with last-login timestamp
- `GET /api/auth/admin/activity` — full org-wide activity feed

Users can see their own activity via `GET /api/auth/activity`.

## Rotating the Gmail app password

If the app password ever needs rotating:
1. Go to https://myaccount.google.com/apppasswords
2. Delete the old `VoLo Earth Auth` entry, create a new one
3. Update the `GMAIL_APP_PASSWORD` secret in Replit
4. Click Redeploy

No code changes needed — the new password is picked up on the next boot.

## Local development

For testing the auth flow locally:
1. Copy `.env.example` to `.env` and fill in the same keys
2. `uvicorn app.main:app --host 127.0.0.1 --port 8001 --env-file .env`
3. If your ISP blocks outbound SMTP (residential ISPs often do), Gmail sends will fail locally. The app falls back to logging verification codes to the server log so you can still exercise the flow.

## Troubleshooting

**"Everyone got logged out after deploy"** → `SECRET_KEY` is not set in Replit Secrets, so uvicorn generates a fresh random key on every boot, which invalidates all existing JWTs. Set `SECRET_KEY` as a Secret.

**"Verification codes never arrive"** →
- Check Replit logs for "Gmail SMTP send failed"
- Most common cause: 2-Step Verification isn't on for the Gmail account, which means the app password won't work. Turn it on, regenerate the app password, update the secret.
- Second most common: typo in `GMAIL_APP_PASSWORD`. Strip spaces — the app handles that — but copy-paste can drop characters.

**"User clicked 'verify' but got 'invalid code'"** → Code expired (15 min). Click "Send a new one" on the verify screen.

**"Password reset link says expired"** → 30 min window. Request another via "Forgot password".

**SQLite locking errors under load** → Confirm `.replit` uses `--workers 1`, not 2. Multi-worker SQLite will fight itself.
