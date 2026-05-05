/* VoLo Mind — Beta tab frontend.
 *
 * All requests live under /api/volomind/*. Failures are caught and surface as
 * a "VoLo Mind temporarily unavailable" panel — the rest of the underwriting
 * UI is unaffected.
 */

(function () {
    'use strict';

    // ------------------------- API helpers -------------------------------

    async function vmGet(path) {
        const r = await fetch(path, { credentials: 'same-origin' });
        if (!r.ok) {
            let detail = await r.text();
            try { detail = JSON.parse(detail).detail || detail; } catch (_) {}
            const err = new Error(`${r.status}: ${detail}`);
            err.status = r.status;
            throw err;
        }
        return r.json();
    }

    async function vmPost(path, body) {
        const r = await fetch(path, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: body === undefined ? undefined : JSON.stringify(body),
        });
        if (!r.ok) {
            let detail = await r.text();
            try { detail = JSON.parse(detail).detail || detail; } catch (_) {}
            const err = new Error(`${r.status}: ${detail}`);
            err.status = r.status;
            throw err;
        }
        return r.json();
    }

    async function vmDelete(path) {
        const r = await fetch(path, { method: 'DELETE', credentials: 'same-origin' });
        if (!r.ok && r.status !== 204) {
            let detail = await r.text();
            try { detail = JSON.parse(detail).detail || detail; } catch (_) {}
            throw new Error(`${r.status}: ${detail}`);
        }
        return true;
    }

    // ------------------------- State ------------------------------------

    const state = {
        initialized: false,
        unavailable: false,
        chatConfigured: false,
        isAdmin: false,
        sources: [],
        threads: [],
        activeThreadId: null,
        messages: [],
        scope: emptyScope(),
        dimensions: {},
        bundle: null,
        sending: false,
    };

    function emptyScope() {
        return {
            verticals: [], sectors: [], stages: [], co_types: [],
            value_chains: [], companies: [], meeting_types: [],
            document_types: [], sources: [],
        };
    }

    // ------------------------- Tab activation ---------------------------

    // Hook into existing switchTab so we initialize on first open.
    function installTabHook() {
        if (typeof window.switchTab !== 'function') {
            // The host's switchTab isn't ready yet; try again shortly.
            setTimeout(installTabHook, 100);
            return;
        }
        const originalSwitch = window.switchTab;
        window.switchTab = function (tab) {
            const result = originalSwitch.apply(this, arguments);
            if (tab === 'volomind' && !state.initialized) {
                initVoLoMind();
            }
            return result;
        };
    }

    async function initVoLoMind() {
        state.initialized = true;
        try {
            const health = await vmGet('/api/volomind/health');
            if (!health.ok) throw new Error(health.error || 'unknown error');
            state.chatConfigured = !!health.chat_configured;
        } catch (e) {
            return showUnavailable(e.message || 'VoLo Mind backend is not responding.');
        }
        // Determine if current user is admin via existing user-badge data
        // (we read from #user-badge which the host populates with role info).
        try {
            const meResp = await fetch('/api/auth/me', { credentials: 'same-origin' });
            if (meResp.ok) {
                const me = await meResp.json();
                state.isAdmin = (me.role === 'admin');
            }
        } catch (_) { /* non-fatal — assume non-admin */ }

        document.getElementById('vm-admin-badge').hidden = !state.isAdmin;
        document.getElementById('vm-admin-controls').hidden = !state.isAdmin;

        await Promise.all([
            refreshSources(),
            refreshDimensions(),
            refreshThreads(),
        ]);
        wireEventHandlers();
    }

    function showUnavailable(detail) {
        state.unavailable = true;
        const main = document.getElementById('vm-main');
        const fallback = document.getElementById('vm-unavailable');
        if (main) main.hidden = true;
        if (fallback) fallback.hidden = false;
        const detailEl = document.getElementById('vm-unavailable-detail');
        if (detailEl && detail) detailEl.textContent = detail;
    }

    // ------------------------- Sources ----------------------------------

    async function refreshSources() {
        try {
            state.sources = await vmGet('/api/volomind/sources');
        } catch (e) {
            state.sources = [];
        }
        renderSources();
    }

    function renderSources() {
        const list = document.getElementById('vm-source-list');
        if (!list) return;
        if (!state.sources.length) {
            list.innerHTML = '<li class="vm-muted">No sources connected yet.</li>';
            return;
        }
        list.innerHTML = state.sources.map(s => `
            <li class="vm-source-row">
                <div class="vm-source-info">
                    <div class="vm-source-label">${escapeHtml(s.label)}</div>
                    <div class="vm-source-meta">
                        ${escapeHtml(s.source_id)} · ${s.document_count} docs${s.last_synced_at ? ' · synced ' + relTime(s.last_synced_at) : ''}
                    </div>
                </div>
                ${state.isAdmin ? `
                    <div class="vm-source-actions">
                        <button class="vm-row-btn" data-act="sync" data-id="${s.id}">sync</button>
                        <button class="vm-row-btn vm-row-btn-danger" data-act="delete" data-id="${s.id}">×</button>
                    </div>
                ` : ''}
            </li>
        `).join('');

        list.querySelectorAll('[data-act="sync"]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const id = btn.dataset.id;
                btn.disabled = true; btn.textContent = '…';
                try {
                    const r = await vmPost(`/api/volomind/sources/${id}/sync`);
                    toast(`Synced: ${r.fetched} fetched, ${r.inserted} new${r.errors.length ? ', ' + r.errors.length + ' errors' : ''}`);
                    if (r.errors.length) console.warn('VoLo Mind sync errors:', r.errors);
                } catch (e) {
                    toast('Sync failed: ' + e.message, 'err');
                } finally {
                    btn.disabled = false; btn.textContent = 'sync';
                    await refreshSources();
                    await refreshDimensions();
                }
            });
        });

        list.querySelectorAll('[data-act="delete"]').forEach(btn => {
            btn.addEventListener('click', async () => {
                if (!confirm('Delete this source and all its synced data?')) return;
                try {
                    await vmDelete(`/api/volomind/sources/${btn.dataset.id}`);
                    await refreshSources();
                    await refreshDimensions();
                } catch (e) {
                    toast('Delete failed: ' + e.message, 'err');
                }
            });
        });
    }

    // ------------------------- Source modal -----------------------------

    function openSourceModal() {
        document.getElementById('vm-source-modal').hidden = false;
        document.getElementById('vm-source-type').value = 'granola';
        document.getElementById('vm-source-label').value = '';
        document.getElementById('vm-drive-folder-id').value = '';
        document.getElementById('vm-drive-co-type').value = '';
        toggleSourceConfigRows();
        // Check Drive connection status for the admin
        refreshDriveStatus();
    }

    function closeSourceModal() {
        document.getElementById('vm-source-modal').hidden = true;
    }

    function toggleSourceConfigRows() {
        const t = document.getElementById('vm-source-type').value;
        document.getElementById('vm-granola-config-row').hidden = (t !== 'granola');
        document.getElementById('vm-drive-config-row').hidden = (t !== 'gdrive_admin');
    }

    async function refreshDriveStatus() {
        const hint = document.getElementById('vm-drive-status-hint');
        if (!hint) return;
        try {
            const status = await vmGet('/api/volomind/admin/drive-status');
            if (status.connected) {
                hint.textContent = `✓ Drive connected as ${status.google_email}. Sync will use these credentials.`;
                hint.className = 'vm-form-hint vm-hint-ok';
            } else {
                hint.textContent = '⚠ Connect Google Drive in the IC Memo tab first — VoLo Mind reuses those credentials.';
                hint.className = 'vm-form-hint vm-hint-warn';
            }
        } catch (e) {
            hint.textContent = 'Drive status check failed: ' + e.message;
            hint.className = 'vm-form-hint vm-hint-warn';
        }
    }

    async function createSource() {
        const t = document.getElementById('vm-source-type').value;
        const label = document.getElementById('vm-source-label').value.trim();
        if (!label) { toast('Label required', 'err'); return; }
        const cfg = {};
        if (t === 'gdrive_admin') {
            const fid = document.getElementById('vm-drive-folder-id').value.trim();
            if (!fid) { toast('Drive folder ID required', 'err'); return; }
            cfg.root_folder_id = parseDriveFolderId(fid);
            const ct = document.getElementById('vm-drive-co-type').value;
            if (ct) cfg.co_type = ct;
        }
        try {
            await vmPost('/api/volomind/sources', { source_id: t, label, config: cfg });
            closeSourceModal();
            await refreshSources();
            toast('Source created. Click "sync" to ingest.');
        } catch (e) {
            toast('Create failed: ' + e.message, 'err');
        }
    }

    function parseDriveFolderId(input) {
        const m = input.match(/folders\/([a-zA-Z0-9_-]+)/);
        if (m) return m[1];
        return input.trim();
    }

    // ------------------------- Scope picker -----------------------------

    async function refreshDimensions() {
        try {
            state.dimensions = await vmGet('/api/volomind/scope/dimensions');
        } catch (_) {
            state.dimensions = {};
        }
        renderScopeRows();
        await refreshBundle();
    }

    const DIM_LABELS = {
        vertical: 'Vertical',
        sector: 'Sector',
        stage: 'Stage',
        co_type: 'Co-type',
        value_chain: 'Value chain',
        company: 'Company',
        meeting_type: 'Meeting type',
        document_type: 'Doc type',
    };

    const DIM_TO_SCOPE = {
        vertical: 'verticals',
        sector: 'sectors',
        stage: 'stages',
        co_type: 'co_types',
        value_chain: 'value_chains',
        company: 'companies',
        meeting_type: 'meeting_types',
        document_type: 'document_types',
    };

    function renderScopeRows() {
        const container = document.getElementById('vm-scope-rows');
        if (!container) return;
        const dims = state.dimensions;
        const dimKeys = Object.keys(DIM_LABELS).filter(k => dims[k] && dims[k].length);
        if (!dimKeys.length) {
            container.innerHTML = '<p class="vm-muted">No tags yet — sync a source to populate filters.</p>';
            return;
        }
        container.innerHTML = dimKeys.map(dim => {
            const scopeKey = DIM_TO_SCOPE[dim];
            const selected = new Set(state.scope[scopeKey] || []);
            const chips = dims[dim].map(v => `
                <button class="vm-chip ${selected.has(v) ? 'vm-chip-active' : ''}"
                        data-dim="${scopeKey}" data-value="${escapeHtml(v)}">
                    ${escapeHtml(v)}
                </button>
            `).join('');
            return `
                <div class="vm-scope-row">
                    <label class="vm-scope-label">${DIM_LABELS[dim]}</label>
                    <div class="vm-chips">${chips}</div>
                </div>
            `;
        }).join('');

        container.querySelectorAll('.vm-chip').forEach(btn => {
            btn.addEventListener('click', () => {
                const dim = btn.dataset.dim;
                const value = btn.dataset.value;
                const list = state.scope[dim] = state.scope[dim] || [];
                const idx = list.indexOf(value);
                if (idx >= 0) list.splice(idx, 1);
                else list.push(value);
                btn.classList.toggle('vm-chip-active');
                refreshBundle();
            });
        });
    }

    async function refreshBundle() {
        try {
            state.bundle = await vmPost('/api/volomind/scope/preview', state.scope);
        } catch (_) {
            state.bundle = null;
        }
        const info = document.getElementById('vm-bundle-info');
        if (!info || !state.bundle) {
            if (info) info.hidden = true;
            return;
        }
        info.hidden = false;
        document.getElementById('vm-bundle-docs').textContent = `${state.bundle.total_documents} docs`;
        document.getElementById('vm-bundle-tokens').textContent = `${state.bundle.total_tokens.toLocaleString()} tokens${state.bundle.truncated ? ' (truncated)' : ''}`;
    }

    function clearScope() {
        state.scope = emptyScope();
        renderScopeRows();
        refreshBundle();
    }

    // ------------------------- Threads ----------------------------------

    async function refreshThreads() {
        try {
            state.threads = await vmGet('/api/volomind/chat/threads');
        } catch (_) {
            state.threads = [];
        }
        renderThreads();
    }

    function renderThreads() {
        const list = document.getElementById('vm-thread-list');
        if (!list) return;
        if (!state.threads.length) {
            list.innerHTML = '<li class="vm-muted">No conversations yet.</li>';
            return;
        }
        list.innerHTML = state.threads.map(t => `
            <li class="vm-thread-row ${t.id === state.activeThreadId ? 'vm-thread-active' : ''}"
                data-id="${t.id}">
                <div class="vm-thread-title">${escapeHtml(t.title)}</div>
                <div class="vm-thread-meta">${relTime(t.created_at)}</div>
            </li>
        `).join('');
        list.querySelectorAll('.vm-thread-row').forEach(row => {
            row.addEventListener('click', () => openThread(parseInt(row.dataset.id)));
        });
    }

    async function newThread() {
        const title = prompt('Conversation title:', `Chat ${new Date().toLocaleDateString()}`);
        if (!title) return;
        try {
            const t = await vmPost('/api/volomind/chat/threads', {
                title,
                scope: state.scope,
            });
            await refreshThreads();
            await openThread(t.id);
        } catch (e) {
            toast('Create thread failed: ' + e.message, 'err');
        }
    }

    async function openThread(id) {
        state.activeThreadId = id;
        renderThreads();
        try {
            state.messages = await vmGet(`/api/volomind/chat/threads/${id}/messages`);
        } catch (e) {
            state.messages = [];
        }
        renderMessages();
        document.getElementById('vm-chat-input').disabled = !state.chatConfigured;
        document.getElementById('vm-chat-send-btn').disabled = !state.chatConfigured;
    }

    function renderMessages() {
        const box = document.getElementById('vm-chat-messages');
        if (!box) return;
        if (!state.messages.length) {
            box.innerHTML = state.chatConfigured
                ? '<div class="vm-chat-empty">Send a message to begin the conversation.</div>'
                : '<div class="vm-chat-empty">Refiant chat client not configured. Set REFIANT_API_KEY/BASE/MODEL in Replit Secrets.</div>';
            return;
        }
        box.innerHTML = state.messages.map(m => `
            <div class="vm-msg vm-msg-${m.role}">
                <div class="vm-msg-role">${m.role}</div>
                <div class="vm-msg-content">${escapeHtml(m.content)}</div>
            </div>
        `).join('');
        box.scrollTop = box.scrollHeight;
    }

    async function sendMessage() {
        if (state.sending || !state.activeThreadId) return;
        const input = document.getElementById('vm-chat-input');
        const text = input.value.trim();
        if (!text) return;
        state.sending = true;
        input.disabled = true;
        document.getElementById('vm-chat-send-btn').disabled = true;
        // optimistic render
        state.messages.push({ id: -1, role: 'user', content: text, created_at: new Date().toISOString() });
        renderMessages();
        input.value = '';
        try {
            const reply = await vmPost(`/api/volomind/chat/threads/${state.activeThreadId}/messages`, { content: text });
            state.messages.push(reply);
            renderMessages();
        } catch (e) {
            toast('Send failed: ' + e.message, 'err');
            state.messages.pop();
            renderMessages();
        } finally {
            state.sending = false;
            input.disabled = false;
            document.getElementById('vm-chat-send-btn').disabled = false;
            input.focus();
        }
    }

    // ------------------------- Wire up ----------------------------------

    function wireEventHandlers() {
        const onClick = (id, fn) => {
            const el = document.getElementById(id);
            if (el) el.addEventListener('click', fn);
        };
        onClick('vm-add-source-btn', openSourceModal);
        onClick('vm-source-modal-close', closeSourceModal);
        onClick('vm-source-modal-cancel', closeSourceModal);
        onClick('vm-source-create-btn', createSource);
        onClick('vm-clear-scope-btn', clearScope);
        onClick('vm-new-thread-btn', newThread);
        onClick('vm-chat-send-btn', sendMessage);

        const typeSel = document.getElementById('vm-source-type');
        if (typeSel) typeSel.addEventListener('change', toggleSourceConfigRows);

        const inp = document.getElementById('vm-chat-input');
        if (inp) inp.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
        });
    }

    // ------------------------- Helpers ----------------------------------

    function escapeHtml(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        })[c]);
    }

    function relTime(iso) {
        if (!iso) return '';
        const t = Date.parse(iso.replace(' ', 'T') + (iso.endsWith('Z') || iso.includes('+') ? '' : 'Z'));
        if (isNaN(t)) return iso;
        const s = Math.max(1, Math.round((Date.now() - t) / 1000));
        if (s < 60) return s + 's ago';
        const m = Math.round(s / 60);
        if (m < 60) return m + 'm ago';
        const h = Math.round(m / 60);
        if (h < 48) return h + 'h ago';
        return Math.round(h / 24) + 'd ago';
    }

    function toast(msg, kind) {
        // Reuse the host app's toast if present, else fall back to alert.
        if (typeof window.showToast === 'function') return window.showToast(msg, kind);
        if (typeof window.toast === 'function') return window.toast(msg, kind);
        console[kind === 'err' ? 'error' : 'log'](`[VoLo Mind] ${msg}`);
    }

    // ------------------------- Boot -------------------------------------

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', installTabHook);
    } else {
        installTabHook();
    }
})();
