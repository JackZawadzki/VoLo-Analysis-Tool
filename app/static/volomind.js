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
        active: [],
        roadmap: [],
        threads: [],
        activeThreadId: null,
        messages: [],
        scope: emptyScope(),
        dimensions: {},
        bundle: null,
        sending: false,
        // Per-source-pk → polling intervalId (so we can cancel)
        syncPolls: {},
        // Per-source-pk → latest sync_status payload
        syncStatus: {},
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
        const footnote = document.getElementById('vm-source-footnote');
        if (footnote) footnote.hidden = state.isAdmin;  // show only to non-admins

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
            const resp = await vmGet('/api/volomind/sources');
            state.active = resp.active || [];
            state.roadmap = resp.roadmap || [];
            // Seed syncStatus from server-side latest run; pick up polling
            // for any source still running (e.g. user reloaded mid-sync).
            state.active.forEach(s => {
                if (s.sync_status) {
                    state.syncStatus[s.id] = s.sync_status;
                    if (s.sync_status.status === 'running') {
                        ensureSyncPolling(s.id);
                    }
                }
            });
        } catch (e) {
            state.active = [];
            state.roadmap = [];
        }
        renderSources();
    }

    function renderSources() {
        const list = document.getElementById('vm-source-list');
        if (!list) return;
        const sections = [];

        if (!state.active.length && !state.roadmap.length) {
            list.innerHTML = '<li class="vm-muted">No sources configured yet.</li>';
            return;
        }

        if (state.active.length) {
            sections.push('<li class="vm-source-section-label">ACTIVE</li>');
            sections.push(...state.active.map(renderActiveSource));
        }
        if (state.roadmap.length) {
            sections.push('<li class="vm-source-section-label">COMING SOON</li>');
            sections.push(...state.roadmap.map(renderRoadmapItem));
        }
        list.innerHTML = sections.join('');

        list.querySelectorAll('[data-act="sync"]').forEach(btn => {
            btn.addEventListener('click', () => triggerSync(parseInt(btn.dataset.id)));
        });

        // Re-attach polling for any sources that were already syncing
        // (e.g. user reloaded the page mid-sync)
        state.active.forEach(s => {
            if (state.syncStatus[s.id] && state.syncStatus[s.id].status === 'running') {
                ensureSyncPolling(s.id);
            }
        });
    }

    async function triggerSync(sourcePk) {
        try {
            await vmPost(`/api/volomind/sources/${sourcePk}/sync`);
            // Optimistic: mark as running immediately
            state.syncStatus[sourcePk] = { status: 'running', fetched: 0, inserted: 0 };
            renderSources();
            ensureSyncPolling(sourcePk);
        } catch (e) {
            toast('Sync failed to start: ' + e.message, 'err');
        }
    }

    function ensureSyncPolling(sourcePk) {
        if (state.syncPolls[sourcePk]) return;  // already polling
        const tick = async () => {
            try {
                const status = await vmGet(`/api/volomind/sources/${sourcePk}/sync-status`);
                state.syncStatus[sourcePk] = status;
                renderSources();
                if (status.status !== 'running') {
                    clearInterval(state.syncPolls[sourcePk]);
                    delete state.syncPolls[sourcePk];
                    // Refresh corpus-derived data once sync ends
                    await refreshSources();
                    await refreshDimensions();
                    if (status.status === 'complete') {
                        toast(`Sync complete: ${status.fetched} fetched, ${status.inserted} new${status.last_error ? ' (with errors)' : ''}`);
                    } else if (status.status === 'error') {
                        toast('Sync error: ' + (status.last_error || 'unknown'), 'err');
                    } else if (status.status === 'interrupted') {
                        toast('Sync interrupted (container restart). Click sync to resume.', 'err');
                    }
                }
            } catch (e) {
                console.warn('sync-status poll failed:', e);
            }
        };
        // Tick once immediately, then every 3 seconds.
        tick();
        state.syncPolls[sourcePk] = setInterval(tick, 3000);
    }

    function renderActiveSource(s) {
        const status = state.syncStatus[s.id];
        const isRunning = status && status.status === 'running';
        const progressLine = isRunning
            ? `<div class="vm-source-progress">syncing… ${status.fetched || 0} fetched, ${status.inserted || 0} new</div>`
            : '';
        const buttonLabel = isRunning ? '…' : 'sync';
        const buttonAttr = isRunning ? 'disabled' : '';
        return `
            <li class="vm-source-row">
                <div class="vm-source-info">
                    <div class="vm-source-label">${escapeHtml(s.label)}</div>
                    <div class="vm-source-meta">
                        ${escapeHtml(s.source_id)} · ${s.document_count} docs${s.last_synced_at ? ' · synced ' + relTime(s.last_synced_at) : ' · never synced'}
                    </div>
                    ${progressLine}
                </div>
                ${state.isAdmin ? `
                    <div class="vm-source-actions">
                        <button class="vm-row-btn" data-act="sync" data-id="${s.id}" ${buttonAttr}>${buttonLabel}</button>
                    </div>
                ` : ''}
            </li>
        `;
    }

    function renderRoadmapItem(item) {
        return `
            <li class="vm-source-row vm-source-coming-soon">
                <div class="vm-source-info">
                    <div class="vm-source-label">${escapeHtml(item.label)}</div>
                    <div class="vm-source-meta">${escapeHtml(item.description || '')}</div>
                </div>
                <span class="vm-coming-soon-pill">soon</span>
            </li>
        `;
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
        onClick('vm-clear-scope-btn', clearScope);
        onClick('vm-new-thread-btn', newThread);
        onClick('vm-chat-send-btn', sendMessage);

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
