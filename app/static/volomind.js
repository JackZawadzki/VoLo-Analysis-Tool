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

    async function vmPatch(path, body) {
        const r = await fetch(path, {
            method: 'PATCH',
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
            sectors: [],            // cascades from vertical
            themes: [],             // cross-cutting climate-tech themes
            technologies: [],       // legacy alias of themes (v2 compat)
            // Secondary / legacy fields kept on the object so API calls
            // serialize cleanly. Not surfaced in the main scope card UI.
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
                sectors: {
                    Energy: ['Solar', 'Wind', 'Geothermal', 'Hydropower', 'Storage / Batteries', 'Hydrogen', 'Nuclear / SMR', 'Grid / Transmission', 'Biofuels / Bioenergy', 'Carbon Capture', 'Fusion'],
                    Buildings: ['HVAC', 'Heat Pumps', 'Insulation / Envelope', 'Lighting / Controls', 'Smart Building', 'Embodied Carbon', 'Building-Integrated Renewables'],
                    Industry: ['Steel', 'Cement / Concrete', 'Chemicals', 'Plastics', 'Mining / Metals', 'Process Heat', 'Industrial Hydrogen', 'Direct Air Capture', 'Industrial AI'],
                    Mobility: ['EVs / Powertrains', 'Charging Infrastructure', 'Batteries', 'Aviation', 'Maritime / Shipping', 'Rail', 'Logistics / Freight', 'Autonomy', 'Micromobility', 'Hydrogen Fuel Cells'],
                },
                themes: ['Electrification', 'Carbon Management', 'Critical Minerals', 'Circular Economy', 'Climate Adaptation', 'Industrial Decarbonization'],
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
            el.classList.remove('vm-tier2-complete', 'vm-tier2-error');
            // Determinate bar — we know done/total. Cap at 99% until the
            // run actually flips to 'complete' so the bar doesn't sit at
            // 100% while the last company is in flight.
            //
            // Stage label: pre-fetch ("starting…") shows before the
            // company list is loaded, then transitions to per-company.
            const total = Math.max(1, t.total || 1);
            const pct = t.total
                ? Math.min(99, Math.round(((t.done || 0) / total) * 100))
                : 5;
            const stage = t.total
                ? `Step 2 of 2 — Classifying ${t.done}/${t.total} companies`
                : 'Step 1 of 2 — Loading company list…';
            const detail = t.current ? `Currently: ${t.current}` : '';
            el.innerHTML = `
                <div class="vm-stage-label">${escapeHtml(stage)}</div>
                <div class="vm-progress-bar"><div class="vm-progress-bar-fill" style="width: ${pct}%"></div></div>
                ${detail ? `<div class="vm-stage-detail">${escapeHtml(detail)}</div>` : ''}
            `;
        } else if (t.status === 'complete' && t.stats) {
            // Persistent completion state — does NOT auto-hide. User
            // dismisses it by clicking sync again or refreshing. This is
            // the explicit "you're done" signal you asked for.
            el.hidden = false;
            el.classList.add('vm-tier2-complete');
            el.classList.remove('vm-tier2-error');
            const s = t.stats;
            el.innerHTML = `
                <div class="vm-stage-label">✓ Classification complete</div>
                <div class="vm-progress-bar"><div class="vm-progress-bar-fill" style="width: 100%"></div></div>
                <div class="vm-stage-detail">Classified ${s.classified} new · skipped ${s.skipped_already_classified} already-tagged${s.failed ? ' · ' + s.failed + ' failed' : ''}</div>
            `;
        } else if (t.status === 'error') {
            el.hidden = false;
            el.classList.add('vm-tier2-error');
            el.classList.remove('vm-tier2-complete');
            el.innerHTML = `
                <div class="vm-stage-label">✗ Classification failed</div>
                <div class="vm-stage-detail">${escapeHtml(t.error || 'unknown error')} — ANTHROPIC_API_KEY in Replit Secrets?</div>
            `;
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

    // Derive a human-readable stage label + bar mode from a sync run row.
    // Backend doesn't report explicit stages — we infer from fetched/inserted
    // transitions: nothing fetched yet = "Connecting / listing", fetched > 0
    // but no inserts = "Downloading", inserts climbing = "Processing".
    function syncStageFromStatus(status) {
        if (!status) return null;
        if (status.status === 'running') {
            const fetched = status.fetched || 0;
            const inserted = status.inserted || 0;
            if (fetched === 0) {
                return { stage: 'Connecting & listing documents…', detail: '' };
            }
            if (inserted === 0) {
                return {
                    stage: 'Downloading documents…',
                    detail: `${fetched} fetched`,
                };
            }
            return {
                stage: 'Processing & saving…',
                detail: `${fetched} fetched · ${inserted} new`,
            };
        }
        if (status.status === 'complete') {
            return {
                stage: '✓ Sync complete',
                detail: `${status.fetched || 0} fetched · ${status.inserted || 0} new${status.last_error ? ' · with errors' : ''}`,
            };
        }
        if (status.status === 'error') {
            return {
                stage: '✗ Sync failed',
                detail: status.last_error || 'unknown error',
            };
        }
        if (status.status === 'interrupted') {
            return {
                stage: '⚠ Sync interrupted',
                detail: 'Container restarted. Click sync to resume from last checkpoint.',
            };
        }
        return null;
    }

    function renderActiveSource(s) {
        const status = state.syncStatus[s.id];
        const isRunning = status && status.status === 'running';
        const isComplete = status && status.status === 'complete';
        const isError = status && (status.status === 'error' || status.status === 'interrupted');
        // During a live sync, prefer the in-flight insert count (truth) over
        // the page-load cached document_count (stale). Once the sync ends and
        // refreshSources runs, document_count catches up.
        const liveDocCount = isRunning && (status.inserted || 0) > s.document_count
            ? status.inserted
            : s.document_count;
        const lastSyncedLabel = isRunning
            ? 'syncing now'
            : (s.last_synced_at ? 'synced ' + relTime(s.last_synced_at) : 'never synced');
        // Sync uses an indeterminate bar — total file count isn't known
        // until after the folder walk completes (which happens mid-sync),
        // and even once files come in we don't have a reliable total
        // estimate. Animated stripe signals active work; on completion we
        // show a persistent filled bar so the user can clearly see "done".
        const stageInfo = syncStageFromStatus(status);
        let progressBlock = '';
        if (isRunning && stageInfo) {
            progressBlock = `
                <div class="vm-stage-label">${escapeHtml(stageInfo.stage)}</div>
                <div class="vm-progress-bar vm-progress-indeterminate"><div class="vm-progress-bar-fill"></div></div>
                ${stageInfo.detail ? `<div class="vm-stage-detail">${escapeHtml(stageInfo.detail)}</div>` : ''}
            `;
        } else if (isComplete && stageInfo) {
            progressBlock = `
                <div class="vm-stage-label vm-stage-complete">${escapeHtml(stageInfo.stage)}</div>
                <div class="vm-progress-bar"><div class="vm-progress-bar-fill" style="width: 100%"></div></div>
                <div class="vm-stage-detail">${escapeHtml(stageInfo.detail)}</div>
            `;
        } else if (isError && stageInfo) {
            progressBlock = `
                <div class="vm-stage-label vm-stage-error">${escapeHtml(stageInfo.stage)}</div>
                <div class="vm-stage-detail">${escapeHtml(stageInfo.detail)}</div>
            `;
        }
        const buttonLabel = isRunning ? '…' : 'sync';
        const buttonAttr = isRunning ? 'disabled' : '';
        return `
            <li class="vm-source-row">
                <div class="vm-source-info">
                    <div class="vm-source-label">${escapeHtml(s.label)}</div>
                    <div class="vm-source-meta">
                        ${escapeHtml(s.source_id)} · ${liveDocCount} docs · ${lastSyncedLabel}
                    </div>
                    ${progressBlock}
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

    // The six primary investor-scoping rows.
    // Sector cascades from vertical: hidden until a vertical is selected,
    // then shows only the selected vertical(s)' sectors.
    // Theme is cross-cutting (always shown).
    // Company / meeting type / doc type / co_type are NOT in this list —
    // they're "advanced filters" that the main scope UI does not surface.
    const FIXED_DIMS = [
        { scope: 'verticals',     label: 'Vertical',     taxonomyKey: 'verticals' },
        { scope: 'sectors',       label: 'Sector',       taxonomyKey: 'sectors', cascadesFrom: 'verticals' },
        { scope: 'stages',        label: 'Stage',        taxonomyKey: 'stages' },
        { scope: 'company_types', label: 'Company type', taxonomyKey: 'company_types' },
        { scope: 'value_chains',  label: 'Value chain',  taxonomyKey: 'value_chains' },
        { scope: 'themes',        label: 'Theme',        taxonomyKey: 'themes' },
    ];

    function renderScopeRows() {
        const container = document.getElementById('vm-scope-rows');
        if (!container) return;
        const taxonomy = state.taxonomy || {};
        const selectedVerticals = state.scope.verticals || [];
        const rows = [];

        for (const f of FIXED_DIMS) {
            let values;
            if (f.scope === 'sectors') {
                // Cascading row — hidden until a vertical is selected.
                if (!selectedVerticals.length) continue;
                const sectorMap = taxonomy.sectors || {};
                values = selectedVerticals.flatMap(v => sectorMap[v] || []);
            } else {
                values = taxonomy[f.taxonomyKey] || [];
            }
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
                // Cascade: when verticals change, prune sectors that no
                // longer belong to any selected vertical (avoids invisible
                // filters silently affecting bundle counts).
                if (scopeKey === 'verticals') {
                    const sectorMap = (state.taxonomy || {}).sectors || {};
                    const validSectors = new Set(
                        (state.scope.verticals || []).flatMap(v => sectorMap[v] || [])
                    );
                    state.scope.sectors = (state.scope.sectors || [])
                        .filter(s => validSectors.has(s));
                    // Re-render so the sector row appears/disappears/refreshes
                    renderScopeRows();
                } else {
                    btn.classList.toggle('vm-chip-active');
                }
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

    // Build a display string from a scope object. Used by:
    //   - default chat name generator
    //   - in-modal scope summary
    //   - chat header breadcrumb
    // Format: "Energy / Solar / Series A" — slashes between dimensions,
    // commas inside multi-select. Empty scope returns null so callers can
    // pick the right "unscoped" wording for their context.
    function formatScopeSummary(scope) {
        if (!scope) return null;
        const parts = [];
        for (const f of FIXED_DIMS) {
            const sel = scope[f.scope] || [];
            if (sel.length) parts.push(sel.join(', '));
        }
        return parts.length ? parts.join(' / ') : null;
    }

    function defaultChatName(scope) {
        const summary = formatScopeSummary(scope);
        return summary || 'Unscoped VoLo Mind Chat';
    }

    function renderThreads() {
        const list = document.getElementById('vm-thread-list');
        if (!list) return;
        if (!state.threads.length) {
            list.innerHTML = '<li class="vm-muted">No conversations yet.</li>';
            return;
        }
        list.innerHTML = state.threads.map(t => {
            const summary = formatScopeSummary(t.scope);
            const subtitle = summary || 'Unscoped';
            return `
                <li class="vm-thread-row ${t.id === state.activeThreadId ? 'vm-thread-active' : ''}"
                    data-id="${t.id}">
                    <div class="vm-thread-main" data-act="open" data-id="${t.id}">
                        <div class="vm-thread-title" title="${escapeHtml(t.title)}">${escapeHtml(t.title)}</div>
                        <div class="vm-thread-meta">
                            <span class="vm-thread-scope" title="${escapeHtml(subtitle)}">${escapeHtml(subtitle)}</span>
                            <span> · ${relTime(t.created_at)}</span>
                        </div>
                    </div>
                    <div class="vm-thread-actions">
                        <button class="vm-icon-btn" data-act="rename" data-id="${t.id}" title="Rename">✎</button>
                        <button class="vm-icon-btn vm-icon-btn-danger" data-act="delete" data-id="${t.id}" title="Delete">×</button>
                    </div>
                </li>
            `;
        }).join('');

        // Click handler — clicks on the title area open the thread; clicks
        // on the per-row buttons fire their own actions and stop propagation
        // so they don't also open the thread underneath.
        list.querySelectorAll('[data-act="open"]').forEach(el => {
            el.addEventListener('click', () => openThread(parseInt(el.dataset.id)));
        });
        list.querySelectorAll('[data-act="rename"]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                renameThread(parseInt(btn.dataset.id));
            });
        });
        list.querySelectorAll('[data-act="delete"]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                deleteThread(parseInt(btn.dataset.id));
            });
        });
    }

    // ─────────────── Naming modal — opens on "Start scoped chat" ────────────
    // Captures the scope at modal-open time so subsequent chip changes don't
    // affect the chat that's about to be created. Esc + outside-click + close
    // button all dismiss it; Enter creates.
    let _pendingScope = null;

    function openNameModal() {
        _pendingScope = JSON.parse(JSON.stringify(state.scope));
        const input = document.getElementById('vm-name-input');
        const summaryEl = document.getElementById('vm-name-scope-summary');
        const modal = document.getElementById('vm-name-modal');
        if (!input || !modal) return;
        input.value = defaultChatName(_pendingScope);
        if (summaryEl) {
            const summary = formatScopeSummary(_pendingScope);
            summaryEl.textContent = summary || 'No filters selected — chat will see the full corpus.';
        }
        modal.hidden = false;
        // setTimeout so the focus call happens after the browser paints the
        // unhidden element; otherwise focus() can no-op on display:none.
        setTimeout(() => { input.focus(); input.select(); }, 0);
    }

    function closeNameModal() {
        const modal = document.getElementById('vm-name-modal');
        if (modal) modal.hidden = true;
        _pendingScope = null;
    }

    async function confirmCreateThread() {
        const input = document.getElementById('vm-name-input');
        const scope = _pendingScope || state.scope;
        const title = (input && input.value.trim()) || defaultChatName(scope);
        try {
            const t = await vmPost('/api/volomind/chat/threads', { title, scope });
            closeNameModal();
            await refreshThreads();
            await openThread(t.id);
        } catch (e) {
            toast('Create thread failed: ' + e.message, 'err');
        }
    }

    async function renameThread(id) {
        const thread = state.threads.find(x => x.id === id);
        if (!thread) return;
        const next = window.prompt('Rename chat:', thread.title);
        if (next === null) return;          // user hit Cancel
        const trimmed = next.trim();
        if (!trimmed || trimmed === thread.title) return;
        try {
            const updated = await vmPatch(`/api/volomind/chat/threads/${id}`, { title: trimmed });
            // Patch local state so the UI updates immediately without
            // an extra round-trip. refreshThreads() runs anyway for safety.
            const idx = state.threads.findIndex(x => x.id === id);
            if (idx >= 0) state.threads[idx] = updated;
            renderThreads();
            renderChatScopeBar();
        } catch (e) {
            toast('Rename failed: ' + e.message, 'err');
        }
    }

    async function deleteThread(id) {
        const thread = state.threads.find(x => x.id === id);
        const label = thread ? thread.title : 'this chat';
        if (!window.confirm(`Delete "${label}"? This removes only your chat history. Synced documents and tags are unaffected.`)) {
            return;
        }
        try {
            await vmDelete(`/api/volomind/chat/threads/${id}`);
            state.threads = state.threads.filter(x => x.id !== id);
            if (state.activeThreadId === id) {
                state.activeThreadId = null;
                state.messages = [];
                renderMessages();
                renderChatScopeBar();
            }
            renderThreads();
        } catch (e) {
            toast('Delete failed: ' + e.message, 'err');
        }
    }

    async function openThread(id) {
        state.activeThreadId = id;
        // Restore the scope chips to the thread's saved scope so the user
        // can see exactly what filters this conversation is using. This is
        // visual-only — sending a message in this thread always uses the
        // thread's locked-in scope (see chat_engine._prepare_call), not
        // the live chip state. Editing chips while an old thread is open
        // affects only the *next* "Start scoped chat" you create.
        const thread = state.threads.find(t => t.id === id);
        if (thread && thread.scope) {
            // Deep-clone — shallow spread would alias the inner arrays
            // (e.g. state.scope.verticals === thread.scope.verticals), so
            // a subsequent chip click would silently mutate the cached
            // thread's subtitle in the sidebar. The thread's actual scope
            // is frozen server-side at creation.
            const cloned = JSON.parse(JSON.stringify(thread.scope));
            state.scope = { ...emptyScope(), ...cloned };
            renderScopeRows();
            refreshBundle();
        }
        renderThreads();
        renderChatScopeBar();
        try {
            state.messages = await vmGet(`/api/volomind/chat/threads/${id}/messages`);
        } catch (e) {
            state.messages = [];
        }
        renderMessages();
        document.getElementById('vm-chat-input').disabled = !state.chatConfigured;
        document.getElementById('vm-chat-send-btn').disabled = !state.chatConfigured;
    }

    function renderChatScopeBar() {
        const bar = document.getElementById('vm-chat-scope-bar');
        const txt = document.getElementById('vm-chat-scope-text');
        if (!bar || !txt) return;
        if (!state.activeThreadId) {
            bar.hidden = true;
            return;
        }
        const thread = state.threads.find(t => t.id === state.activeThreadId);
        if (!thread) {
            bar.hidden = true;
            return;
        }
        const summary = formatScopeSummary(thread.scope);
        bar.hidden = false;
        txt.textContent = summary || 'Unscoped — full corpus';
    }

    function renderMessages() {
        const box = document.getElementById('vm-chat-messages');
        if (!box) return;
        if (!state.messages.length) {
            box.innerHTML = state.chatConfigured
                ? '<div class="vm-chat-empty">Send a message to begin the conversation.</div>'
                : '<div class="vm-chat-empty">Refiant chat client not configured. An admin must set REFIANT_API_KEY in Replit Secrets.</div>';
            return;
        }
        box.innerHTML = state.messages.map(m => {
            // 'system' role is reserved for inline error bubbles — show
            // "error" as the visible label so it's not confusing.
            const label = m.role === 'system' ? 'error' : m.role;
            return `
                <div class="vm-msg vm-msg-${m.role}">
                    <div class="vm-msg-role">${escapeHtml(label)}</div>
                    <div class="vm-msg-content">${escapeHtml(m.content)}</div>
                </div>
            `;
        }).join('');
        box.scrollTop = box.scrollHeight;
    }

    function friendlySendError(e) {
        // Map the various ways a Refiant call can fail to clear, actionable
        // messages. Status comes from vmPost's err.status.
        const status = e && e.status;
        const detail = (e && e.message) || 'unknown error';
        if (status === 501) {
            return 'Refiant chat client not configured. An admin must set REFIANT_API_KEY in Replit Secrets.';
        }
        if (status === 400) {
            return `Chat config issue: ${detail}`;
        }
        if (status === 404) {
            return 'This conversation no longer exists. Refresh the page.';
        }
        if (status === 502) {
            return `Refiant API call failed — ${detail}. Try again in a moment, or refine your scope to fit the model context window.`;
        }
        return `Send failed: ${detail}`;
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
            const msg = friendlySendError(e);
            toast(msg, 'err');
            // Remove the optimistically added user bubble and surface the
            // error inline so the chat history shows what just happened
            // (toasts can be missed if the page scrolls or the user blinks).
            state.messages.pop();
            state.messages.push({
                id: -2, role: 'system',
                content: msg,
                created_at: new Date().toISOString(),
            });
            renderMessages();
            // Drop the inline error after the next successful send so it
            // doesn't pollute chat history persistently.
            setTimeout(() => {
                state.messages = state.messages.filter(m => m.id !== -2);
                renderMessages();
            }, 12000);
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
        onClick('vm-scope-chat-btn', openNameModal);
        onClick('vm-chat-send-btn', sendMessage);
        onClick('vm-tier2-run-btn', () => triggerTier2(false));

        // Naming modal wiring
        onClick('vm-name-modal-close', closeNameModal);
        onClick('vm-name-cancel', closeNameModal);
        onClick('vm-name-create', confirmCreateThread);
        const modal = document.getElementById('vm-name-modal');
        if (modal) {
            // Click on the overlay (but not the card) closes.
            modal.addEventListener('click', (e) => {
                if (e.target === modal) closeNameModal();
            });
        }
        const nameInput = document.getElementById('vm-name-input');
        if (nameInput) {
            nameInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') { e.preventDefault(); confirmCreateThread(); }
                if (e.key === 'Escape') { e.preventDefault(); closeNameModal(); }
            });
        }
        // Global Escape handler — closes whichever modal is open.
        document.addEventListener('keydown', (e) => {
            if (e.key !== 'Escape') return;
            const m = document.getElementById('vm-name-modal');
            if (m && !m.hidden) closeNameModal();
        });

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
