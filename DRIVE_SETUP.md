# Google Drive Integration Setup

The RVM can sync data room documents directly from Google Drive folders. This uses a **service account** (no OAuth login flow required).

## 1. Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use an existing one)
3. Enable the **Google Drive API**: APIs & Services > Library > search "Google Drive API" > Enable

## 2. Create a Service Account

1. Go to APIs & Services > Credentials
2. Click "Create Credentials" > "Service Account"
3. Name it something like `rvm-drive-reader`
4. Skip the optional permissions steps
5. Click on the service account > Keys > Add Key > Create New Key > JSON
6. Save the downloaded JSON file (e.g., `service-account.json`)

## 3. Configure the RVM

Add one of these to your `.env` file:

```bash
# Option A: Path to the JSON key file
GOOGLE_SERVICE_ACCOUNT_JSON=/path/to/service-account.json

# Option B: Inline JSON (useful for Docker/cloud deployments)
GOOGLE_SERVICE_ACCOUNT_CREDS='{"type":"service_account","project_id":"...","private_key":"...","client_email":"..."}'
```

## 4. Share Drive Folders with the Service Account

The service account has its own email address (looks like `rvm-drive-reader@your-project.iam.gserviceaccount.com`).

For each deal folder you want to sync:

1. Open the Google Drive folder
2. Click "Share"
3. Add the service account email with **Viewer** access
4. Uncheck "Notify people" and click Share

## 5. Install Python Dependencies

```bash
pip install google-api-python-client google-auth
```

## 6. Usage

1. Go to the **Investment Memo** tab
2. In the "Data Room Documents" section, click **+** next to the library selector
3. Enter the company name and paste the Google Drive folder URL
4. Click **Link Folder**, then **Sync from Drive**
5. The RVM will recursively pull all documents, extract text, and cache locally
6. Subsequent syncs only download new or changed files
7. When generating a memo, the library documents are automatically included

## Supported File Types

Directly extractable: PDF, DOCX, XLSX, PPTX, CSV, TXT, MD, JSON, HTML

Google Workspace files are auto-exported: Google Docs → DOCX, Google Sheets → XLSX, Google Slides → PPTX

## Notes

- The service account only needs **read** access (Viewer role)
- Document text is cached in the local SQLite database — no re-extraction on subsequent memo generations
- Re-syncing detects changed files by comparing `modifiedTime` from Drive
- Files deleted from Drive are automatically removed from the library on next sync
- Subfolders are traversed recursively; the subfolder path is preserved in the document metadata
