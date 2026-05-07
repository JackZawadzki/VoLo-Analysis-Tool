/**
 * Drive Sync card — shown on the Portfolio Review index page.
 *
 * States:
 *   1. Checking — initial spinner while we hit /connection-status
 *   2. Not connected — shows "Connect Google Drive" button (kicks OAuth flow)
 *   3. Connected — shows the user's email + a search box that lists their
 *      spreadsheets. Clicking one runs an import and shows the result.
 *
 * Uses the existing /api/drive OAuth flow (same one the underwriting tool
 * uses for the Drive integration). No new consent step.
 */
class DriveSyncCard {
  constructor(rootEl) {
    this.root = rootEl;
    this.statusEl = rootEl.querySelector('#drive-status-badge');
    this.contentEl = rootEl.querySelector('#drive-sync-content');
    this.refresh();
  }

  authHeaders(extra = {}) {
    const t = localStorage.getItem('rvm_token') || '';
    const h = { ...extra };
    if (t) h['Authorization'] = 'Bearer ' + t;
    return h;
  }

  async refresh() {
    try {
      const r = await fetch('/api/drive/connection-status', { headers: this.authHeaders() });
      if (!r.ok) {
        this.renderError(`Couldn't check Drive status (HTTP ${r.status}).`);
        return;
      }
      const status = await r.json();
      if (status.connected) {
        this.renderConnected(status);
      } else {
        this.renderDisconnected();
      }
    } catch (e) {
      this.renderError(e.message);
    }
  }

  setBadge(text, classes) {
    this.statusEl.textContent = text;
    this.statusEl.className = `text-xs px-2 py-0.5 rounded ml-auto ${classes}`;
  }

  renderError(msg) {
    this.setBadge('error', 'bg-red-100 text-red-700');
    this.contentEl.innerHTML = `<div class="text-red-600">${msg}</div>`;
  }

  renderDisconnected() {
    this.setBadge('not connected', 'bg-gray-100 text-gray-600');
    this.contentEl.innerHTML = `
      <p class="mb-3">Connect your Google account to pull the latest workbook directly from Drive.
        Each user signs in with their own account and only sees files they already have access to.</p>
      <a href="/api/drive/oauth/authorize?return_to=/portfolio-review/"
         class="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded text-sm font-medium hover:bg-blue-700">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="white" xmlns="http://www.w3.org/2000/svg">
          <path d="M12 5v14M5 12h14" stroke="white" stroke-width="2"/>
        </svg>
        Connect Google Drive
      </a>
      <p class="text-xs text-gray-400 mt-2">If you see "Connection refused" or a 500 error, the
        admin still needs to add <code>GOOGLE_OAUTH_CLIENT_ID</code> / <code>SECRET</code> to <code>.env</code>.</p>`;
  }

  renderConnected(status) {
    const email = status.google_email || 'connected';
    this.setBadge(email, 'bg-green-100 text-green-800');
    this.contentEl.innerHTML = `
      <div class="flex items-center gap-3 mb-3">
        <input id="drive-search" type="text" placeholder="Search your spreadsheets…"
          class="flex-1 px-3 py-2 border border-gray-200 rounded text-sm focus:outline-none focus:border-blue-500">
        <button id="drive-search-btn"
          class="px-3 py-2 text-sm bg-gray-900 text-white rounded hover:bg-black">Search</button>
        <button id="drive-disconnect"
          class="px-3 py-2 text-sm text-gray-500 hover:text-red-600">Disconnect</button>
      </div>
      <div id="drive-results" class="text-sm"></div>
      <div id="drive-import-result" class="mt-3 text-sm"></div>`;

    const searchEl = this.contentEl.querySelector('#drive-search');
    const btn = this.contentEl.querySelector('#drive-search-btn');
    const disconnectBtn = this.contentEl.querySelector('#drive-disconnect');

    btn.addEventListener('click', () => this.search(searchEl.value));
    searchEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') this.search(searchEl.value); });
    disconnectBtn.addEventListener('click', () => this.disconnect());

    // Auto-load a default search
    this.search('Portfolio Review');
  }

  async disconnect() {
    if (!confirm('Disconnect Google Drive? You can reconnect anytime.')) return;
    await fetch('/api/drive/disconnect', { method: 'POST', headers: this.authHeaders() });
    this.refresh();
  }

  async search(query) {
    const resultsEl = this.contentEl.querySelector('#drive-results');
    resultsEl.innerHTML = '<div class="text-gray-400">Searching…</div>';
    try {
      const url = '/api/portfolio-review/drive/files' + (query ? `?q=${encodeURIComponent(query)}` : '');
      const r = await fetch(url, { headers: this.authHeaders() });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        resultsEl.innerHTML = `<div class="text-red-600">Search failed: ${this._esc(err.detail || r.statusText)}</div>`;
        return;
      }
      const data = await r.json();
      const files = data.files || [];
      if (!files.length) {
        resultsEl.innerHTML = `<div class="text-gray-500">No matching spreadsheets in your Drive.</div>`;
        return;
      }
      resultsEl.innerHTML = `
        <div class="text-xs text-gray-500 mb-2">${files.length} spreadsheet${files.length===1?'':'s'} — click to import</div>
        <div class="space-y-1">${files.map(f => this._renderFile(f)).join('')}</div>`;
      resultsEl.querySelectorAll('[data-file-id]').forEach(el => {
        el.addEventListener('click', () => this.importFile(el.dataset.fileId, el.dataset.fileName));
      });
    } catch (e) {
      resultsEl.innerHTML = `<div class="text-red-600">${this._esc(e.message)}</div>`;
    }
  }

  _renderFile(f) {
    const isSheet = f.mimeType === 'application/vnd.google-apps.spreadsheet';
    const icon = isSheet ? '🟩' : '📊';
    const owner = (f.owners && f.owners[0] && f.owners[0].displayName) || '';
    const dt = f.modifiedTime ? new Date(f.modifiedTime).toLocaleDateString() : '';
    return `
      <button data-file-id="${this._esc(f.id)}" data-file-name="${this._esc(f.name)}"
        class="w-full text-left px-3 py-2 rounded border border-gray-200 hover:bg-gray-50 hover:border-blue-300 flex items-center gap-3">
        <span>${icon}</span>
        <div class="flex-1 min-w-0">
          <div class="font-medium text-gray-900 truncate">${this._esc(f.name)}</div>
          <div class="text-xs text-gray-500">${this._esc(owner)} · modified ${this._esc(dt)} · ${isSheet ? 'Google Sheets' : 'Excel'}</div>
        </div>
        <span class="text-xs text-blue-600">Import →</span>
      </button>`;
  }

  async importFile(fileId, fileName) {
    const out = this.contentEl.querySelector('#drive-import-result');
    out.innerHTML = `<div class="text-gray-500">Downloading and importing "${this._esc(fileName)}"… (~30s)</div>`;
    try {
      const r = await fetch(`/api/portfolio-review/drive/sync?file_id=${encodeURIComponent(fileId)}`, {
        method: 'POST', headers: this.authHeaders(),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        out.innerHTML = `<div class="text-red-600">Import failed: ${this._esc(err.detail || r.statusText)}</div>`;
        return;
      }
      const result = await r.json();
      const counts = result.counts || {};
      out.innerHTML = `
        <div class="bg-green-50 border border-green-200 rounded p-3">
          <div class="font-medium text-green-900">Imported "${this._esc(fileName)}" ✓</div>
          <div class="text-sm text-green-800 mt-1">
            ${counts.companies || 0} companies · ${counts.investments || 0} investments ·
            ${counts.returns || 0} returns · ${counts.board_seats || 0} board seats
          </div>
          <button onclick="window.location.reload()" class="mt-2 text-sm text-green-700 underline">Reload page to see new data</button>
        </div>`;
    } catch (e) {
      out.innerHTML = `<div class="text-red-600">${this._esc(e.message)}</div>`;
    }
  }

  _esc(s) {
    return (s || '').toString().replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }
}
