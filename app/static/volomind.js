/* VoLo Mind — Beta tab frontend.
 *
 * All requests live under /api/volomind/*. Failures are caught and surface as
 * a "VoLo Mind temporarily unavailable" panel — the rest of the underwriting
 * UI is unaffected.
 */

(function () {
    'use strict';

    // ------------------------- API helpers -------------------------------
    // Host app uses JWT in localStorage('rvm_token') -> Authorization header.
    // We mirror that here so volomind routes (which use the same auth dep)
    // receive the token. Without this, every volomind fetch 401s.

    function vmAuthHeaders() {
        const token = localStorage.getItem('rvm_token') || '';
        return token ? { 'Authorization': 'Bearer ' + token } : {};
    }

    async function vmGet(path) {
        const r = await fetch(path, {
            credentials: 'same-origin',
            headers: vmAuthHeaders(),
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

    async function vmPost(path, body) {
        const r = await fetch(path, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', ...vmAuthHeaders() },
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
        const r = await fetch(path, {
            method: 'DELETE',
            credentials: 'same-origin',
            headers: vmAuthHeaders(),
        });
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
        dimensions: {},     // dynamic dims (company, doc_type, etc.) from corpus
        taxonomy: null,     // fixed VoLo taxonomy from /scope/taxonomy
        bundle: null,
        sending: false,
        syncPolls: {},
        syncStatus: {},
        tier2: { status: 'idle', done: 0, total: 0, current: '' },
        tier2Poll: null,
    };

    function emptyScope() {
        return {
            // Primary investor-scoping dimensions (Tier 2 LLM tags)
            verticals: [],
            stages: [],
            company_types: [],
            value_chains: [],
            technologies: [],
            // Secondary / legacy fields kept on the object so API calls
            // serialize cleanly. Not surfaced in the main scope card UI.
            sectors: [],
            co_types: [],
            companies: [],
            meeting_types: [],
            document_types: [],
            sources: [],
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
        // Determine if current user is admin. Use vmGet so the auth header
        // is attached the same way the host app's app.js does it (JWT token
        // from localStorage('rvm_token')).
        try {
            const me = await vmGet('/api/auth/me');
            state.isAdmin = (me.role === 'admin');
        } catch (_) { /* non-fatal — assume non-admin */ }

        document.getElementById('vm-admin-badge').hidden = !state.isAdmin;
        const tier2Controls = document.getElementById('vm-tier2-controls');
        if (tier2Controls) tier2Controls.hidden = !state.isAdmin;
        const footnote = document.getElementById('vm-source-footnote');
        if (footnote) footnote.hidden = state.isAdmin;  // show only to non-admins

        await Promise.all([
            refreshSources(),
            refreshTaxonomy(),
            refreshDimensions(),
            refreshThreads(),
            refreshTier2Status(),
        ]);
        wireEventHandlers();
    }

    async function refreshTaxonomy() {
        try {
            state.taxonomy = await vmGet('/api/volomind/scope/taxonomy');
        } catch (e) {
            // Fallback hardcoded so the UI still works if the endpoint fails.
            // Mirror the server-side VOLO_TAXONOMY in tier2_tagger.py.
            state.taxonomy = {
                verticals: ['Energy', 'Buildings', 'Industry', 'Mobility'],
                stages: ['Pre-Seed', 'Seed', 'Series A', 'Series B', 'Series C', 'Growth', 'Public / Incumbent'],
                company_types: [
                    'Software', 'Hardware', 'Hardware-Enabled Software', 'Materials',
                    'Infrastructure / Project Developer', 'Marketplace / Network',
                    'Financing / Insurance', 'Services',
                ],
                value_chains: [
                    'Raw Materials', 'Materials Processing / Refining',
                    'Component / Equipment Manufacturing', 'System Integration',
                    'Project Development / Deployment', 'Operations & Maintenance',
                    'Monitoring / Measurement / Verification',
                    'Optimization / Control Software', 'Asset Management',
                    'Market / Grid / System Operations', 'Financing / Risk / Insurance',
                    'Carbon Accounting / Reporting',
                    'Circularity / Recycling / End-of-Life', 'Workforce / Services',
                ],
                technologies: [
                    'Electrification', 'Grid', 'Storage', 'Renewables', 'Nuclear',
                    'Hydrogen', 'Carbon Management', 'Critical Minerals',
                    'Industrial Decarbonization', 'Building Efficiency',
                    'Heat Pumps / HVAC', 'EVs / Charging', 'Fleet / Logistics',
                    'Circular Economy', 'Climate Adaptation',
                ],
            };
        }
    }

    async function refreshTier2Status() {
        try {
            state.tier2 = await vmGet('/api/volomind/tier2/status');
            // If a run is in progress, start polling
            if (state.tier2.status === 'running' && !state.tier2Poll) {
                state.tier2Poll = setInterval(refreshTier2Status, 4000);
            } else if (state.tier2.status !== 'running' && state.tier2Poll) {
                clearInterval(state.tier2Poll);
                state.tier2Poll = null;
                // Refresh dimensions in case Tier 2 just finished and added new tag values
                refreshDimensions();
            }
        } catch (_) { /* non-fatal */ }
        renderTier2Status();
    }

    function renderTier2Status() {
        const el = document.getElementById('vm-tier2-status');
        if (!el) return;
        const t = state.tier2 || {};
        if (t.status === 'running') {
            el.hidden = false;
            el.textContent = `Classifying ${t.done}/${t.total} companies… ${t.current ? '(' + t.current + ')' : ''}`;
        } else if (t.status === 'complete' && t.stats) {
            el.hidden = false;
            el.textContent = `Classified ${t.stats.classified} new, skipped ${t.stats.skipped_already_classified} already-tagged.`;
            setTimeout(() => { el.hidden = true; }, 8000);
        } else if (t.status === 'error') {
            el.hidden = false;
            el.textContent = `Tier 2 error: ${t.error || 'unknown'}`;
        } else {
            el.hidden = true;
        }
    }

    async function triggerTier2(force) {
        try {
            await vmPost('/api/volomind/tier2/run' + (force ? '?force=true' : ''));
            state.tier2 = { ...state.tier2, status: 'running', done: 0, total: 0, current: '' };
            renderTier2Status();
            if (!state.tier2Poll) {
                state.tier2Poll = setInterval(refreshTier2Status, 4000);
            }
        } catch (e) {
            toast('Tier 2 start failed: ' + e.message, 'err');
        }
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
        // During a live sync, prefer the in-flight insert count (truth) over
        // the page-load cached document_count (stale). Once the sync ends and
        // refreshSources runs, document_count catches up.
        const liveDocCount = isRunning && (status.inserted || 0) > s.document_count
            ? status.inserted
            : s.document_count;
        const lastSyncedLabel = isRunning
            ? 'syncing now'
            : (s.last_synced_at ? 'synced ' + relTime(s.last_synced_at) : 'never synced');
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
                        ${escapeHtml(s.source_id)} · ${liveDocCount} docs · ${lastSyncedLabel}
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

    // The five primary investor-scoping rows. All always rendered from the
    // hardcoded taxonomy (single source of truth lives server-side in
    // tier2_tagger.VOLO_TAXONOMY; the frontend caches a copy for offline
    // robustness and to avoid an extra round-trip per render).
    //
    // Company / meeting type / doc type / co_type are NOT in this list —
    // they're "advanced filters" that the main scope UI does not surface.
    // They remain on the ScopeFilter API contract for future use.
    const FIXED_DIMS = [
        { scope: 'verticals',     label: 'Vertical',         taxonomyKey: 'verticals' },
        { scope: 'stages',        label: 'Stage',            taxonomyKey: 'stages' },
        { scope: 'company_types', label: 'Company type',     taxonomyKey: 'company_types' },
        { scope: 'value_chains',  label: 'Value chain',      taxonomyKey: 'value_chains' },
        { scope: 'technologies',  label: 'Technology / theme', taxonomyKey: 'technologies' },
    ];

    function renderScopeRows() {
        const container = document.getElementById('vm-scope-rows');
        if (!container) return;
        const taxonomy = state.taxonomy || {};
        const rows = [];

        for (const f of FIXED_DIMS) {
            const values = taxonomy[f.taxonomyKey] || [];
            if (!values.length) continue;
            const selected = new Set(state.scope[f.scope] || []);
            const chips = values.map(v => `
                <button class="vm-chip ${selected.has(v) ? 'vm-chip-active' : ''}"
                        data-scope="${f.scope}" data-value="${escapeHtml(v)}">
                    ${escapeHtml(v)}
                </button>
            `).join('');
            rows.push(`
                <div class="vm-scope-row">
                    <label class="vm-scope-label">${f.label}</label>
                    <div class="vm-chips">${chips}</div>
                </div>
            `);
        }

        container.innerHTML = rows.join('');

        container.querySelectorAll('.vm-chip').forEach(btn => {
            btn.addEventListener('click', () => {
                const scopeKey = btn.dataset.scope;
                const value = btn.dataset.value;
                const list = state.scope[scopeKey] = state.scope[scopeKey] || [];
                const idx = list.indexOf(value);
                if (idx >= 0) list.splice(idx, 1);
                else list.push(value);
                // Within-row toggle is OR; across rows is AND. The chip's
                // visual state updates next render. Refresh the bundle
                // preview so the doc/token counts reflect the new scope.
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
        onClick('vm-tier2-run-btn', () => triggerTier2(false));

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
