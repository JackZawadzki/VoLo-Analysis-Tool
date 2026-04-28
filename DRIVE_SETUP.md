# Google Drive Integration Setup

VoLo Engine reads deal-room folders directly from Google Drive using **per-user
OAuth**. Each team member signs in with their own Google account and the app
sees only the folders they personally have access to — same as if they opened
Drive themselves. No shared service account, no per-folder sharing step.

## What you need

A Google Workspace domain (we use `voloearth.com`) and one-time access to
[Google Cloud Console](https://console.cloud.google.com) with permission to
create a project there.

## 1. Create a Google Cloud project

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Top-left project dropdown → **New Project**
3. Name it `VoLo Engine`, set Organization to `voloearth.com`, click **Create**
4. Switch into the new project (top-left dropdown)

## 2. Enable the Google Drive API

1. Left menu: **APIs & Services → Library**
2. Search "Google Drive API" → click → **Enable**

## 3. Configure the OAuth consent screen

The Google Cloud UI was reorganized — these settings now live under
**Google Auth Platform** in the left menu (the lock-shield icon). Old guides
still say "OAuth consent screen"; same thing, new home.

1. **Audience**: set User Type to **Internal**. This skips Google verification
   and limits sign-ins to `@voloearth.com` accounts only.
2. **Branding**: app name `VoLo Engine`, support email and developer email
   filled in.
3. **Data Access** → **Add or Remove Scopes** → search `drive` → check
   `https://www.googleapis.com/auth/drive.readonly` → **Update** → **Save**.

## 4. Create the OAuth Client

1. **Clients → Create Client**
2. Application type: **Web application**
3. Name: `VoLo Engine Web Client`
4. **Authorized redirect URIs** — add both:
   - `https://vo-lo-analysis-tool.replit.app/api/drive/oauth/callback`
   - `http://localhost:8000/api/drive/oauth/callback` (for local dev)
5. Click **Create**

A popup shows the **Client ID** and **Client Secret**. Copy both.

## 5. Add the credentials to your environment

The app needs three environment variables. On Replit, add these as Secrets;
locally, add them to `.env`.

```bash
# From step 4
GOOGLE_OAUTH_CLIENT_ID=<the long client ID ending in .apps.googleusercontent.com>
GOOGLE_OAUTH_CLIENT_SECRET=<the secret>

# Generate once with the command below — keep stable across deploys.
GOOGLE_TOKEN_ENCRYPTION_KEY=<a Fernet key>
```

Generate the encryption key with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

This key encrypts each user's stored Drive refresh token at rest. If you ever
rotate it, every existing connection becomes unreadable and users will need to
click Connect again — no data loss, just a one-click re-auth.

`GOOGLE_OAUTH_REDIRECT_URI` is optional; the app derives it from the request
host automatically, which works on both Replit and localhost.

## 6. Install the Python libraries

If you're updating an existing checkout, the new packages are in
`requirements.txt`:

```bash
pip install -r requirements.txt
```

This installs `google-api-python-client`, `google-auth`, `google-auth-oauthlib`,
and `cryptography`.

## 7. Connect from the app

After the server restarts with the new env vars:

1. Go to **IC Memo** tab
2. Section **2. Data Room Documents → Google Drive** shows a "Connect Google
   Drive" button
3. Click it — Google's consent screen opens, you sign in with your
   `@voloearth.com` account, click **Allow**
4. You're redirected back to the IC Memo tab. The Google Drive section now
   shows "Connected as your.name@voloearth.com" with a Disconnect button
5. Paste a Drive folder URL → **Link Folder** → **Sync from Drive**

The sync recursively walks subfolders, downloads supported file types, and
extracts text into the local DB so memo generation can use it.

## What the app can read

- Anything **you personally** have access to in Drive: your own files, files
  shared with you directly, files in shared drives where you're a member.
- Read-only: the app cannot edit, delete, or upload anything to your Drive.

If a folder doesn't appear or sync fails with permission errors, it means
**your** Google account doesn't have access to it — share the folder with
yourself in Drive (same as you would with a colleague), then re-sync.

## Supported file types

Directly extractable: PDF, DOCX, XLSX, PPTX, CSV, TXT, MD, JSON, HTML.

Google Workspace files are auto-exported on download:
- Google Docs → DOCX
- Google Sheets → XLSX
- Google Slides → PPTX

## Disconnecting / re-authorizing

- **In the app**: IC Memo → Google Drive → Disconnect button. Drops the stored
  refresh token immediately.
- **From your Google account**: [myaccount.google.com/permissions](https://myaccount.google.com/permissions)
  → find "VoLo Engine" → Remove access. The app's next call will fail; the
  next time you visit IC Memo, the Connect button reappears.

## Security notes

- Refresh tokens are encrypted at rest with `GOOGLE_TOKEN_ENCRYPTION_KEY`
  (Fernet / AES-128-CBC + HMAC-SHA256).
- The CSRF state parameter on the OAuth callback is a signed JWT using the
  app's `SECRET_KEY` with a 10-minute TTL — prevents OAuth code injection.
- Each user's tokens are isolated; the server never reads another user's
  Drive on their behalf.
- Read-only scope (`drive.readonly`) — the app cannot modify anyone's Drive.
