/**
 * Granola sync card + Drive folder discovery button for the Portfolio
 * Review homepage. Wires the buttons to the backend endpoints
 * (POST /api/portfolio-review/granola/sync, /traction/discover-folders,
 * GET /api/portfolio-review/granola/syncs) and renders status inline.
 *
 * Auth: relies on the page already being signed in. The shared
 * `_authHeaders()` helper grabs the JWT the same way drive_sync.js does
 * — kept duplicated rather than imported because each card file boots
 * independently from the index template.
 */

function _prAuthHeaders() {
  // Mirrors DriveSyncCard.authHeaders — token lives in localStorage under
  // the same key the IC-memo auth flow in app.js writes to.
  const tok = localStorage.getItem('rvm_token') || '';
  const h = { 'Content-Type': 'application/json' };
  if (tok) h['Authorization'] = `Bearer ${tok}`;
  return h;
}


/* ─────────────────────────────────────────────────────────────────────
   Discover company folders — bulk-links every subfolder of the
   default parent (PORTFOLIO_DRIVE_PARENT_FOLDER_ID, defaults to the
   Portfolio Company Information shared drive) to existing
   pr_companies by name match. Idempotent — re-run any time.
   ───────────────────────────────────────────────────────────────────── */
class DiscoverFoldersButton {
  constructor() {
    this.btn = document.getElementById('discover-folders-btn');
    this.resultEl = document.getElementById('discover-folders-result');
    if (!this.btn) return;
    this.btn.addEventListener('click', () => this.run());
  }

  async run() {
    if (!this.resultEl) return;
    this.btn.disabled = true;
    this.btn.textContent = 'Discovering…';
    this.resultEl.innerHTML = '<span class="text-gray-500">Walking the Portfolio Company Information folder…</span>';

    try {
      // No parent_folder_id query param → backend uses the default
      // (PORTFOLIO_DRIVE_PARENT_FOLDER_ID env var, falls back to the
      // shared drive root).
      const r = await fetch('/api/portfolio-review/traction/discover-folders?folder_type=current', {
        method: 'POST',
        headers: _prAuthHeaders(),
      });
      const data = await r.json();
      if (!r.ok) {
        const detail = data && (data.detail || data.error) || `HTTP ${r.status}`;
        throw new Error(detail);
      }

      const matched = data.matched ?? 0;
      const unmatched = data.unmatched ?? [];
      const total = data.total_subfolders ?? 0;
      this.resultEl.innerHTML = `
        <div class="text-green-700">
          ✓ Walked ${total} subfolder${total === 1 ? '' : 's'}.
          Linked <strong>${matched}</strong> to existing companies.
          ${unmatched.length > 0
            ? `<br><span class="text-amber-700 text-xs">
                ${unmatched.length} subfolder${unmatched.length === 1 ? '' : 's'} couldn't be matched —
                check spelling against pr_companies.name or rename in Drive.
              </span>`
            : ''}
        </div>`;
    } catch (e) {
      this.resultEl.innerHTML = `<span class="text-red-600">Discovery failed: ${e.message}</span>`;
    } finally {
      this.btn.disabled = false;
      this.btn.textContent = 'Discover folders';
    }
  }
}


/* ─────────────────────────────────────────────────────────────────────
   Granola sync card — pulls notes from the configured allowlist of
   Granola folders (Investment Committee, Portco Updates, Screening +
   Rapid Fire Meeting by default), matches each to portfolio companies
   by attendee email or name-in-title, and writes associations to
   pr_granola_notes. Re-running upserts; new notes added, existing
   updated.
   ───────────────────────────────────────────────────────────────────── */
class GranolaSyncCard {
  constructor(rootEl) {
    this.root = rootEl;
    if (!this.root) return;
    this.btn = document.getElementById('granola-sync-btn');
    this.badge = document.getElementById('granola-status-badge');
    this.lastSyncEl = document.getElementById('granola-last-sync');
    this.resultEl = document.getElementById('granola-sync-result');
    if (this.btn) this.btn.addEventListener('click', () => this.run());
    this.loadLastSync();
  }

  setBadge(text, classes) {
    if (!this.badge) return;
    this.badge.textContent = text;
    this.badge.className = `text-xs px-2 py-0.5 rounded ml-auto ${classes}`;
  }

  async loadLastSync() {
    try {
      const r = await fetch('/api/portfolio-review/granola/syncs?limit=1', {
        headers: _prAuthHeaders(),
      });
      if (!r.ok) {
        // Not signed in or server problem — leave the UI quiet.
        this.setBadge('not run yet', 'bg-gray-100 text-gray-500');
        return;
      }
      const rows = await r.json();
      if (!Array.isArray(rows) || rows.length === 0) {
        this.setBadge('not run yet', 'bg-gray-100 text-gray-500');
        if (this.lastSyncEl) this.lastSyncEl.textContent = '';
        return;
      }
      const latest = rows[0];
      const when = latest.finished_at || latest.started_at || '';
      const status = latest.status || 'unknown';
      const badgeCls = status === 'success'
        ? 'bg-green-100 text-green-700'
        : status === 'failed'
          ? 'bg-red-100 text-red-700'
          : 'bg-amber-100 text-amber-700';
      this.setBadge(`last: ${status}`, badgeCls);
      if (this.lastSyncEl && when) {
        const human = new Date(when.replace(' ', 'T') + 'Z').toLocaleString();
        this.lastSyncEl.textContent = `Last sync: ${human} · ${latest.notes_fetched || 0} notes fetched · ${latest.associations_new || 0} new links`;
      }
    } catch (_e) {
      // Quiet — non-critical
    }
  }

  async run() {
    if (!this.resultEl) return;
    this.btn.disabled = true;
    this.btn.textContent = 'Syncing…';
    this.resultEl.innerHTML = '<span class="text-gray-500">Pulling notes from Granola…</span>';

    try {
      const r = await fetch('/api/portfolio-review/granola/sync', {
        method: 'POST',
        headers: _prAuthHeaders(),
      });
      const data = await r.json();
      if (!r.ok) {
        const detail = data && (data.detail || data.error) || `HTTP ${r.status}`;
        throw new Error(detail);
      }

      const lines = [];
      lines.push(`✓ Fetched ${data.notes_fetched || 0} notes from Granola`);
      lines.push(`<span class="text-gray-600">${data.notes_in_scope || 0} were in the configured folders</span>`);
      lines.push(`<strong>${data.associations_new || 0}</strong> new company links`);
      if (data.associations_updated) {
        lines.push(`${data.associations_updated} existing links refreshed`);
      }
      if (data.associations_unmatched) {
        lines.push(`<span class="text-amber-700 text-xs">${data.associations_unmatched} note${data.associations_unmatched === 1 ? '' : 's'} couldn't be matched to any company</span>`);
      }
      this.resultEl.innerHTML = `<div class="text-green-700">${lines.join('<br>')}</div>`;
      // Refresh the badge + last-sync line.
      this.loadLastSync();
    } catch (e) {
      this.resultEl.innerHTML = `<span class="text-red-600">Granola sync failed: ${e.message}</span>`;
      this.setBadge('error', 'bg-red-100 text-red-700');
    } finally {
      this.btn.disabled = false;
      this.btn.textContent = 'Sync Granola notes';
    }
  }
}
