/**
 * Per-section view renderer. Each section has its own data-fetching strategy
 * and column layout. Currently implemented: returns, valuation, governance,
 * inputs, summary. Other sections show a placeholder until backed by data.
 */
class SectionView {
  constructor(rootEl, slug) {
    this.root = rootEl;
    this.slug = slug;
    this.render();
  }

  async render() {
    const renderers = {
      'returns':       () => this.renderReturns(),
      'valuation':     () => this.renderValuation(),
      'governance':    () => this.renderGovernance(),
      'inputs':        () => this.renderInputs(),
      'summary':       () => this.renderSummary(),
      'composition':   () => this.renderPlaceholder('Sector / Geo / Graduation breakdowns', 'composition'),
      'traction':      () => this.renderTraction(),
      'derisking':     () => this.renderDerisking(),
      'follow-on':     () => this.renderPlaceholder('Bridges, conversions, priced rounds', 'follow-on'),
      'directory':     () => this.renderDirectory(),
      'carta-source':  () => this.renderPlaceholder('Raw paste targets — bound to Drive once OAuth is connected', 'carta'),
    };
    const fn = renderers[this.slug] || (() => this.renderPlaceholder('This section is not yet implemented.', this.slug));
    await fn();
  }

  setHTML(html) { this.root.innerHTML = html; }

  fmtMoney(n) { return n == null ? '—' : '$' + Math.round(n).toLocaleString(); }
  fmtMoneyMm(n) { return n == null ? '—' : '$' + (n / 1e6).toFixed(2) + 'M'; }
  fmtMult(n) { return n == null ? '—' : (Math.round(n * 100) / 100) + 'x'; }
  fmtPct(n) { return n == null ? '—' : (Math.round(n * 1000) / 10) + '%'; }
  esc(s) { return (s || '').toString().replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

  async fetchJson(url) {
    const token = localStorage.getItem('rvm_token') || '';
    const headers = token ? { 'Authorization': 'Bearer ' + token } : {};
    const r = await fetch(url, { headers });
    if (!r.ok) {
      if (r.status === 401) throw new Error('Sign in required — open http://localhost:8000/ and log in first');
      throw new Error(r.statusText);
    }
    return await r.json();
  }

  async renderReturns() {
    try {
      const data = await this.fetchJson('/api/portfolio-review/returns');
      const rows = data.returns || [];
      this.setHTML(`
        <div class="px-5 py-3 border-b border-gray-200 flex items-center">
          <div class="font-semibold">Returns by company</div>
          <div class="ml-auto text-xs text-gray-500">${rows.length} entries</div>
        </div>
        <div class="overflow-x-auto">
          <table class="pr-table">
            <thead><tr>
              <th>Company</th><th>Fund</th><th>As of</th>
              <th class="pr-num">Cost</th><th class="pr-num">FMV</th>
              <th class="pr-num">Total Value</th><th class="pr-num">Multiple</th><th class="pr-num">IRR</th>
            </tr></thead>
            <tbody>${rows.map(r => `
              <tr>
                <td><a class="pr-link" href="/portfolio-review/company/${r.company_id}">${this.esc(r.company_name)}</a></td>
                <td class="text-gray-500">${this.esc(r.fund)}</td>
                <td class="text-gray-500">${this.esc(r.as_of_date)}</td>
                <td class="pr-num">${this.fmtMoney(r.cost)}</td>
                <td class="pr-num">${this.fmtMoney(r.fmv)}</td>
                <td class="pr-num">${this.fmtMoney(r.total_value)}</td>
                <td class="pr-num font-semibold">${this.fmtMult(r.multiple)}</td>
                <td class="pr-num">${this.fmtPct(r.irr)}</td>
              </tr>`).join('') || '<tr><td colspan="8" class="text-gray-500 p-6">No return data — run the Excel sync to populate.</td></tr>'}
            </tbody>
          </table>
        </div>`);
    } catch (e) {
      this.setHTML(`<div class="p-6 text-sm text-red-600">${this.esc(e.message)}</div>`);
    }
  }

  async renderValuation() {
    try {
      const data = await this.fetchJson('/api/portfolio-review/dashboard');
      const tops = data.top_holdings || [];
      this.setHTML(`
        <div class="px-5 py-3 border-b border-gray-200 font-semibold">Valuation & Ownership</div>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4 p-5">
          <div><div class="text-xs text-gray-500 uppercase">Total invested</div><div class="text-2xl font-bold">${this.fmtMoneyMm(data.total_invested)}</div></div>
          <div><div class="text-xs text-gray-500 uppercase">Total FMV</div><div class="text-2xl font-bold">${this.fmtMoneyMm(data.total_fmv)}</div></div>
          <div><div class="text-xs text-gray-500 uppercase">Companies</div><div class="text-2xl font-bold">${data.n_companies}</div></div>
        </div>
        <div class="px-5 py-3 border-t border-b border-gray-200 font-semibold">Top 5 holdings (by FMV)</div>
        <table class="pr-table">
          <thead><tr><th>Company</th><th>Fund</th><th class="pr-num">Cost</th><th class="pr-num">FMV</th><th class="pr-num">Multiple</th></tr></thead>
          <tbody>${tops.map(t => `
            <tr>
              <td>${this.esc(t.name)}</td>
              <td class="text-gray-500">${this.esc(t.fund)}</td>
              <td class="pr-num">${this.fmtMoney(t.cost)}</td>
              <td class="pr-num">${this.fmtMoney(t.fmv)}</td>
              <td class="pr-num font-semibold">${this.fmtMult(t.multiple)}</td>
            </tr>`).join('') || '<tr><td colspan="5" class="text-gray-500 p-6">No data.</td></tr>'}
          </tbody>
        </table>`);
    } catch (e) {
      this.setHTML(`<div class="p-6 text-sm text-red-600">${this.esc(e.message)}</div>`);
    }
  }

  async renderGovernance() {
    try {
      const data = await this.fetchJson('/api/portfolio-review/board-seats');
      const rows = data.board_seats || [];
      this.setHTML(`
        <div class="px-5 py-3 border-b border-gray-200 font-semibold">Active board seats (${rows.length})</div>
        <table class="pr-table">
          <thead><tr><th>Company</th><th>Fund</th><th>Seat type</th><th>Board member</th></tr></thead>
          <tbody>${rows.map(b => `
            <tr>
              <td><a class="pr-link" href="/portfolio-review/company/${b.company_id}">${this.esc(b.company_name)}</a></td>
              <td class="text-gray-500">${this.esc(b.fund)}</td>
              <td><span class="text-xs px-2 py-0.5 rounded ${b.seat_type === 'Director' ? 'bg-blue-100 text-blue-800' : 'bg-gray-100 text-gray-700'}">${this.esc(b.seat_type)}</span></td>
              <td>${this.esc(b.board_member)}</td>
            </tr>`).join('') || '<tr><td colspan="4" class="text-gray-500 p-6">No active board seats recorded.</td></tr>'}
          </tbody>
        </table>`);
    } catch (e) {
      this.setHTML(`<div class="p-6 text-sm text-red-600">${this.esc(e.message)}</div>`);
    }
  }

  async renderInputs() {
    try {
      const data = await this.fetchJson('/api/portfolio-review/companies');
      const rows = data.companies || [];
      this.setHTML(`
        <div class="px-5 py-3 border-b border-gray-200 flex items-center">
          <div class="font-semibold">All companies (${rows.length})</div>
        </div>
        <table class="pr-table">
          <thead><tr><th>Name</th><th>Fund</th><th>Sector</th><th>Status</th><th>CEO</th></tr></thead>
          <tbody>${rows.map(c => `
            <tr>
              <td><a class="pr-link" href="/portfolio-review/company/${c.id}">${this.esc(c.name)}</a></td>
              <td class="text-gray-500">${this.esc(c.fund)}</td>
              <td class="text-gray-500">${this.esc(c.sector)}</td>
              <td class="text-gray-500">${this.esc(c.commercial_status)}</td>
              <td class="text-gray-500">${this.esc(c.ceo_name)}</td>
            </tr>`).join('') || '<tr><td colspan="5" class="text-gray-500 p-6">No companies — run the Excel sync to populate.</td></tr>'}
          </tbody>
        </table>`);
    } catch (e) {
      this.setHTML(`<div class="p-6 text-sm text-red-600">${this.esc(e.message)}</div>`);
    }
  }

  async renderSummary() {
    try {
      const data = await this.fetchJson('/api/portfolio-review/dashboard');
      this.setHTML(`
        <div class="px-5 py-3 border-b border-gray-200 font-semibold">Portfolio summary</div>
        <div class="grid grid-cols-1 md:grid-cols-4 gap-4 p-5">
          <div><div class="text-xs text-gray-500 uppercase">Companies</div><div class="text-3xl font-bold">${data.n_companies}</div></div>
          <div><div class="text-xs text-gray-500 uppercase">Investments</div><div class="text-3xl font-bold">${data.n_investments}</div></div>
          <div><div class="text-xs text-gray-500 uppercase">Total invested</div><div class="text-3xl font-bold">${this.fmtMoneyMm(data.total_invested)}</div></div>
          <div><div class="text-xs text-gray-500 uppercase">Total FMV</div><div class="text-3xl font-bold">${this.fmtMoneyMm(data.total_fmv)}</div></div>
        </div>`);
    } catch (e) {
      this.setHTML(`<div class="p-6 text-sm text-red-600">${this.esc(e.message)}</div>`);
    }
  }

  async renderDirectory() {
    try {
      const data = await this.fetchJson('/api/portfolio-review/companies');
      const rows = data.companies || [];
      this.setHTML(`
        <div class="px-5 py-3 border-b border-gray-200 font-semibold">Contact directory</div>
        <table class="pr-table">
          <thead><tr><th>Company</th><th>CEO</th><th>CEO email</th><th>CFO</th></tr></thead>
          <tbody>${rows.map(c => `
            <tr>
              <td>${this.esc(c.name)}</td>
              <td>${this.esc(c.ceo_name) || '—'}</td>
              <td class="text-gray-500">${this.esc(c.ceo_email) || '—'}</td>
              <td class="text-gray-500">${this.esc(c.cfo_name) || '—'}</td>
            </tr>`).join('')}
          </tbody>
        </table>`);
    } catch (e) {
      this.setHTML(`<div class="p-6 text-sm text-red-600">${this.esc(e.message)}</div>`);
    }
  }

  async renderTraction() {
    try {
      const [data, derisk] = await Promise.all([
        this.fetchJson('/api/portfolio-review/traction'),
        this.fetchJson('/api/portfolio-review/derisking').catch(() => ({ scores: [] })),
      ]);
      const snapshots = data.snapshots || [];
      const fundraising = data.fundraising || [];

      // Build a quick lookup: company_id → derisking score
      const deriskByCompany = {};
      for (const d of derisk.scores || []) {
        deriskByCompany[d.company_id] = d;
      }

      const statusBadge = (s) => {
        const colors = {
          'Pre-Rev':     'bg-gray-200 text-gray-700',
          'Pilot':       'bg-yellow-100 text-yellow-800',
          'Commercial':  'bg-green-100 text-green-800',
          'Hyperscale':  'bg-blue-100 text-blue-800',
        };
        return `<span class="text-xs font-semibold px-2 py-0.5 rounded ${colors[s] || 'bg-gray-100 text-gray-600'}">${this.esc(s) || '—'}</span>`;
      };

      // Group by fund
      const byFund = {};
      for (const s of snapshots) {
        const f = s.fund || 'Other';
        byFund[f] ||= [];
        byFund[f].push(s);
      }

      const quartileBadge = (q) => {
        if (!q) return '<span class="text-gray-300 text-xs">—</span>';
        const colors = {
          1: 'bg-red-100 text-red-800 border-red-300',
          2: 'bg-orange-100 text-orange-800 border-orange-300',
          3: 'bg-yellow-100 text-yellow-800 border-yellow-300',
          4: 'bg-green-100 text-green-800 border-green-300',
        };
        return `<span class="text-xs font-bold px-2 py-0.5 rounded border ${colors[q] || 'bg-gray-100 text-gray-700'}" title="Derisking quartile (1=highest residual risk, 4=most derisked)">Q${q}</span>`;
      };

      const dimensionDots = (d) => {
        if (!d) return '';
        const dims = [
          ['rapid_innovation_adopt', 'Rapid innovation & adoption'],
          ['business_model', 'Business model'],
          ['technology', 'Technology'],
          ['incentive_management', 'Incentive management'],
          ['team', 'Team'],
          ['product_growth', 'Product & growth'],
          ['ip_and_data', 'IP & Data'],
        ];
        return dims.map(([k, label]) => {
          const v = d[k];
          let cls = 'bg-gray-200', sym = '·';
          if (v === 1)  { cls = 'bg-green-500 text-white'; sym = '+'; }
          else if (v === -1) { cls = 'bg-red-500 text-white'; sym = '−'; }
          else if (v === 0)  { cls = 'bg-yellow-300'; sym = '0'; }
          return `<span class="inline-block w-4 h-4 rounded-full text-xs leading-4 text-center ${cls}" title="${this.esc(label)}: ${v == null ? 'unscored' : v}">${sym}</span>`;
        }).join(' ');
      };

      const renderCompanyTable = (fundName, items) => {
        if (!items.length) return '';
        return `
          <div class="pr-card mb-6">
            <div class="px-5 py-3 border-b border-gray-200 flex items-center bg-gray-50">
              <div class="font-semibold text-gray-900">${this.esc(fundName)} Portfolio: Where We Are Today</div>
              <div class="ml-auto text-xs text-gray-500">${items.length} compan${items.length === 1 ? 'y' : 'ies'}</div>
            </div>
            <table class="pr-table">
              <thead><tr>
                <th class="text-right" style="width:40px">#</th>
                <th>Company</th>
                <th>Lead</th>
                <th title="Derisking quartile from the 7-dimension scorecard">Quart.</th>
                <th>Derisking dimensions</th>
                <th>Status</th>
                <th>Update</th>
                <th></th>
              </tr></thead>
              <tbody>${items.map((s, i) => {
                const d = deriskByCompany[s.company_id];
                return `
                <tr>
                  <td class="pr-num text-gray-400">${s.deck_row_index || (i + 1)}</td>
                  <td>
                    <a class="pr-link font-medium" href="/portfolio-review/company/${s.company_id}">${this.esc(s.company_name)}</a>
                    ${s.sector ? `<div class="text-xs text-gray-500">${this.esc(s.sector)}</div>` : ''}
                  </td>
                  <td class="text-sm text-gray-600">${this.esc(s.deal_lead) || '—'}</td>
                  <td>${quartileBadge(d?.quartile)}
                    ${d ? `<div class="text-xs text-gray-500 mt-1">${d.total_score >= 0 ? '+' : ''}${d.total_score}</div>` : ''}
                  </td>
                  <td class="whitespace-nowrap">${dimensionDots(d)}</td>
                  <td>
                    ${statusBadge(s.commercial_status)}
                    ${s.revenue_current ? `<div class="text-xs text-gray-500 mt-1">${this.fmtMoneyMm(s.revenue_current)} ${this.esc(s.revenue_period)}</div>` : ''}
                  </td>
                  <td class="text-sm text-gray-700 max-w-xl leading-relaxed">${this.esc(s.narrative_raw || s.summary)}</td>
                  <td><button class="text-xs text-gray-400 hover:text-blue-600 js-rescan" data-cid="${s.company_id}" title="Rescan">↻</button></td>
                </tr>`;
              }).join('')}
              </tbody>
            </table>
          </div>`;
      };

      const fundraisingHtml = fundraising.length ? `
        <div class="pr-card mb-6 border-l-4" style="border-left-color: #ED7D31;">
          <div class="px-5 py-3 border-b border-gray-200 flex items-center">
            <span class="text-2xl mr-2">💰</span>
            <div class="font-semibold text-gray-900">PortCos Actively Fundraising Now</div>
            <div class="ml-auto text-xs text-gray-500">${fundraising.length}</div>
          </div>
          <ul class="p-5 space-y-1.5 text-sm">${fundraising.map(f => `
            <li>
              <a class="font-medium pr-link" href="/portfolio-review/company/${f.company_id}">${this.esc(f.company_name)}</a>
              <span class="text-gray-400 mx-1">—</span>
              <span class="text-gray-700">${this.esc(f.fundraising_status)}</span>
            </li>`).join('')}
          </ul>
        </div>` : '';

      const headerHtml = `
        <div class="pr-card mb-6 p-5 flex items-center gap-3">
          <div>
            <div class="text-xs text-gray-500 uppercase tracking-wide">Traction & Status reporting</div>
            <div class="text-sm text-gray-700 mt-1">${snapshots.length} compan${snapshots.length === 1 ? 'y' : 'ies'} reported · ${data.unscanned?.length || 0} not yet covered</div>
          </div>
          <div class="ml-auto flex items-center gap-2">
            <label class="text-xs px-3 py-1.5 rounded bg-blue-600 text-white hover:bg-blue-700 cursor-pointer">
              Upload Updates Deck
              <input type="file" id="traction-upload" accept=".pptx" class="hidden">
            </label>
            <label class="text-xs px-3 py-1.5 rounded bg-purple-600 text-white hover:bg-purple-700 cursor-pointer">
              Upload Derisking Sheet
              <input type="file" id="derisking-upload" accept=".xlsx" class="hidden">
            </label>
            <button id="traction-discover-btn" class="text-xs px-3 py-1.5 rounded border border-gray-300 hover:bg-gray-50">Link Drive folders</button>
            <button id="traction-scan-all-btn" class="text-xs px-3 py-1.5 rounded border border-gray-300 hover:bg-gray-50">Scan via Drive</button>
          </div>
        </div>`;

      const emptyHtml = !snapshots.length ? `
        <div class="pr-card p-8 text-center">
          <div class="text-gray-700 font-medium">No traction reports yet.</div>
          <div class="text-sm text-gray-500 mt-2 max-w-lg mx-auto">
            Click <strong>Upload Updates Deck</strong> above to import a Monthly All-Team PortCo Updates .pptx —
            the parser pulls the per-company status rows + the active fundraising list, and Claude extracts
            structured commercial status and revenue from each narrative.
          </div>
        </div>` : '';

      const fundOrder = ['Fund I', 'Fund II', 'AGM', 'SPV'];
      const orderedFunds = fundOrder.filter(f => byFund[f]).concat(
        Object.keys(byFund).filter(f => !fundOrder.includes(f))
      );

      const unscannedHtml = (data.unscanned || []).length ? `
        <div class="pr-card p-4 bg-gray-50 text-sm text-gray-600">
          <span class="font-medium">${data.unscanned.length}</span> compan${data.unscanned.length === 1 ? 'y has' : 'ies have'} no traction snapshot yet:
          <span class="text-gray-500">${data.unscanned.slice(0, 8).map(c => this.esc(c.name)).join(', ')}${data.unscanned.length > 8 ? ` +${data.unscanned.length - 8} more` : ''}</span>
        </div>` : '';

      this.setHTML(
        headerHtml +
        fundraisingHtml +
        orderedFunds.map(f => renderCompanyTable(f, byFund[f])).join('') +
        emptyHtml +
        unscannedHtml
      );

      // Wire up
      document.getElementById('traction-discover-btn')?.addEventListener('click', () => this._discoverFolders());
      document.getElementById('traction-scan-all-btn')?.addEventListener('click', () => this._scanAll());
      document.getElementById('traction-upload')?.addEventListener('change', (e) => this._uploadDeck(e.target.files[0]));
      document.getElementById('derisking-upload')?.addEventListener('change', (e) => this._uploadDerisking(e.target.files[0]));
      this.root.querySelectorAll('.js-rescan').forEach(btn => {
        btn.addEventListener('click', () => this._scanOne(parseInt(btn.dataset.cid), btn));
      });
    } catch (e) {
      this.setHTML(`<div class="p-6 text-sm text-red-600">${this.esc(e.message)}</div>`);
    }
  }

  async _uploadDerisking(file) {
    if (!file) return;
    const out = document.createElement('div');
    out.className = 'pr-card p-4 mb-4 bg-purple-50 text-sm';
    out.innerHTML = `Importing derisking scorecard <strong>${this.esc(file.name)}</strong>…`;
    this.root.prepend(out);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const t = localStorage.getItem('rvm_token') || '';
      const r = await fetch('/api/portfolio-review/derisking/import', {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + t },
        body: fd,
      });
      const data = await r.json();
      if (!r.ok) {
        out.className = 'pr-card p-4 mb-4 bg-red-50 text-sm text-red-700';
        out.innerHTML = `Import failed: ${this.esc(data.detail || r.statusText)}`;
        return;
      }
      const totalScored = (data.results || []).reduce((acc, x) => acc + (x.scored || 0), 0);
      out.className = 'pr-card p-4 mb-4 bg-green-50 text-sm text-green-800';
      out.innerHTML = `Imported ${totalScored} derisking score${totalScored === 1 ? '' : 's'} across ${data.sheets_processed} tab${data.sheets_processed === 1 ? '' : 's'}. Reloading…`;
      setTimeout(() => this.renderTraction(), 1000);
    } catch (e) {
      out.className = 'pr-card p-4 mb-4 bg-red-50 text-sm text-red-700';
      out.innerHTML = `Upload error: ${this.esc(e.message)}`;
    }
  }

  async _uploadDeck(file) {
    if (!file) return;
    const out = document.createElement('div');
    out.className = 'pr-card p-4 mb-4 bg-blue-50 text-sm';
    out.innerHTML = `Importing <strong>${this.esc(file.name)}</strong>… Claude is summarizing each company (~${Math.ceil(20)} sec).`;
    this.root.prepend(out);

    try {
      const fd = new FormData();
      fd.append('file', file);
      const t = localStorage.getItem('rvm_token') || '';
      const r = await fetch('/api/portfolio-review/traction/import-deck', {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + t },
        body: fd,
      });
      const data = await r.json();
      if (!r.ok) {
        out.className = 'pr-card p-4 mb-4 bg-red-50 text-sm text-red-700';
        out.innerHTML = `Import failed: ${this.esc(data.detail || r.statusText)}`;
        return;
      }
      out.className = 'pr-card p-4 mb-4 bg-green-50 text-sm text-green-800';
      out.innerHTML = `Imported ${data.snapshots_added} snapshot${data.snapshots_added === 1 ? '' : 's'} · ${data.matched} matched · ${data.unmatched} unmatched · ${data.fundraising_count} active fundraises tracked. Reloading…`;
      setTimeout(() => this.renderTraction(), 1200);
    } catch (e) {
      out.className = 'pr-card p-4 mb-4 bg-red-50 text-sm text-red-700';
      out.innerHTML = `Upload error: ${this.esc(e.message)}`;
    }
  }

  async _discoverFolders() {
    const parentId = prompt(
      "Paste the Drive folder ID (or full URL) of the parent folder containing your portfolio company subfolders.\n\n" +
      "Example: https://drive.google.com/drive/folders/1AbCdEf123XyZ → use 1AbCdEf123XyZ"
    );
    if (!parentId) return;
    const folderType = prompt(
      "Folder type? Type one of:\n  current      — ongoing materials\n  diligence    — investment-time DD pack\n  board_pack   — quarterly board materials\n\n(Run twice — once with 'current', once with 'diligence')",
      "current"
    );
    if (!folderType) return;
    // Extract ID from URL if needed
    const m = parentId.match(/folders\/([a-zA-Z0-9_-]+)/);
    const cleanId = m ? m[1] : parentId.trim();

    try {
      const t = localStorage.getItem('rvm_token') || '';
      const r = await fetch(
        `/api/portfolio-review/traction/discover-folders?parent_folder_id=${encodeURIComponent(cleanId)}&folder_type=${encodeURIComponent(folderType)}`,
        { method: 'POST', headers: { 'Authorization': 'Bearer ' + t } }
      );
      const data = await r.json();
      if (!r.ok) {
        alert(`Discovery failed: ${data.detail || r.statusText}`);
        return;
      }
      alert(
        `Linked ${data.matched} folder${data.matched === 1 ? '' : 's'} (${folderType}).\n` +
        `${(data.unmatched || []).length} unmatched, ${data.skipped_existing} already linked.\n\n` +
        (data.unmatched && data.unmatched.length ? 'Unmatched:\n' + data.unmatched.slice(0, 10).join('\n') : '')
      );
      this.renderTraction();
    } catch (e) {
      alert('Discovery error: ' + e.message);
    }
  }

  async _scanOne(companyId, btn) {
    const orig = btn ? btn.textContent : '';
    if (btn) { btn.textContent = 'Scanning…'; btn.disabled = true; }
    try {
      const t = localStorage.getItem('rvm_token') || '';
      const r = await fetch(`/api/portfolio-review/traction/scan/${companyId}`, {
        method: 'POST', headers: { 'Authorization': 'Bearer ' + t },
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || r.statusText);
      this.renderTraction();
    } catch (e) {
      alert(`Scan failed: ${e.message}`);
      if (btn) { btn.textContent = orig; btn.disabled = false; }
    }
  }

  async _scanAll() {
    const folders = await this.fetchJson('/api/portfolio-review/traction/folders');
    const ids = [...new Set((folders.folders || []).map(f => f.company_id))];
    if (!ids.length) {
      alert('No companies have folders linked yet. Click "Link folders" first.');
      return;
    }
    if (!confirm(`Scan ${ids.length} companies? Each takes ~10–30s. Total: ~${Math.ceil(ids.length * 0.4)} min.`)) return;
    let done = 0;
    for (const cid of ids) {
      try {
        const t = localStorage.getItem('rvm_token') || '';
        await fetch(`/api/portfolio-review/traction/scan/${cid}`, {
          method: 'POST', headers: { 'Authorization': 'Bearer ' + t },
        });
      } catch {}
      done++;
      if (done % 5 === 0) this.renderTraction();
    }
    this.renderTraction();
    alert(`Scanned ${done} companies.`);
  }

  // ── Derisking Scorecard ──────────────────────────────────────────────────
  // Mirrors the boss's Excel layout: companies grouped by current investment
  // stage, ranked within stage by total derisking score, with a YoY quartile
  // chip pair so you can see who moved up / down between periods. Adds an
  // "AI Score" button per row that calls /derisking/llm-score/{id}.
  async renderDerisking(opts = {}) {
    this.derisking = this.derisking || { provider: 'anthropic', period: null, compare: null };
    if (opts.provider) this.derisking.provider = opts.provider;
    if (opts.period !== undefined)  this.derisking.period  = opts.period;
    if (opts.compare !== undefined) this.derisking.compare = opts.compare;

    let data;
    try {
      const qs = new URLSearchParams();
      if (this.derisking.period)  qs.set('period', this.derisking.period);
      if (this.derisking.compare) qs.set('compare_to', this.derisking.compare);
      data = await this.fetchJson('/api/portfolio-review/derisking/by-stage' + (qs.toString() ? `?${qs}` : ''));
    } catch (e) {
      this.setHTML(`<div class="p-6 text-sm text-red-600">${this.esc(e.message)}</div>`);
      return;
    }
    this.derisking.period  = data.primary_period;
    this.derisking.compare = data.compare_period;

    const periods = data.available_periods || [];
    const periodOptions = (selected) => periods.map(p =>
      `<option value="${this.esc(p)}" ${p === selected ? 'selected' : ''}>${this.esc(p)}</option>`
    ).join('');

    const provider = this.derisking.provider;
    const providerToggle = `
      <div class="inline-flex rounded border border-gray-300 overflow-hidden text-xs" role="group" aria-label="LLM provider">
        <button type="button" data-provider="anthropic"
          class="px-3 py-1.5 ${provider === 'anthropic' ? 'bg-purple-600 text-white' : 'bg-white text-gray-700 hover:bg-gray-50'} js-provider-btn">
          Claude
        </button>
        <button type="button" data-provider="refiant"
          class="px-3 py-1.5 ${provider === 'refiant' ? 'bg-purple-600 text-white' : 'bg-white text-gray-700 hover:bg-gray-50'} js-provider-btn">
          Refiant (QWEN)
        </button>
      </div>`;

    const headerHtml = `
      <div class="pr-card mb-6 p-5">
        <div class="flex items-center gap-3 flex-wrap">
          <div>
            <div class="text-xs text-gray-500 uppercase tracking-wide">Derisking Scorecard</div>
            <div class="text-sm text-gray-700 mt-1">
              7 dimensions × stage-banded ranking. AI scoring reads the IC memo + recent board decks + Granola notes for each company.
            </div>
          </div>
          <div class="ml-auto flex items-center gap-3 flex-wrap">
            <label class="text-xs text-gray-600">Period
              <select id="derisking-period" class="ml-1 text-xs border border-gray-300 rounded px-2 py-1">${periodOptions(data.primary_period)}</select>
            </label>
            <label class="text-xs text-gray-600">Compare to
              <select id="derisking-compare" class="ml-1 text-xs border border-gray-300 rounded px-2 py-1">
                <option value="">— none —</option>
                ${periodOptions(data.compare_period)}
              </select>
            </label>
            <span class="h-5 w-px bg-gray-300"></span>
            <span class="text-xs text-gray-500">LLM:</span>
            ${providerToggle}
          </div>
        </div>
        <div class="mt-3 text-xs text-gray-500 leading-relaxed">
          <strong>How AI scoring works:</strong> For each company, the LLM compares the original IC memo (from the linked
          <em>diligence</em> Drive folder) against the most recent board deck + investor updates and the last few Granola
          meeting notes, then scores each dimension +1/0/-1 with cited evidence. Results are stored under a separate
          period (suffix <code>LLM</code>) so they sit alongside the partners' workbook scores rather than overwriting them.
        </div>
      </div>`;

    const stageHtml = (data.stages || []).map(stage => this._renderDeriskingStage(stage, !!data.compare_period)).join('');

    const emptyHtml = !(data.stages || []).length ? `
      <div class="pr-card p-8 text-center text-gray-600">
        <div class="font-medium">No derisking scores yet.</div>
        <div class="text-sm text-gray-500 mt-2 max-w-md mx-auto">
          Either upload the Derisking Quadrants .xlsx in the Traction tab to import the partners' annual scoring,
          or click <strong>AI Score</strong> on a company in the Traction tab to generate the first LLM-scored row here.
        </div>
      </div>` : '';

    this.setHTML(headerHtml + stageHtml + emptyHtml);

    // Wire up controls
    const onPeriodChange = () => {
      this.renderDerisking({
        period:  document.getElementById('derisking-period').value,
        compare: document.getElementById('derisking-compare').value || null,
      });
    };
    document.getElementById('derisking-period')?.addEventListener('change', onPeriodChange);
    document.getElementById('derisking-compare')?.addEventListener('change', onPeriodChange);
    this.root.querySelectorAll('.js-provider-btn').forEach(btn => {
      btn.addEventListener('click', () => this.renderDerisking({ provider: btn.dataset.provider }));
    });
    this.root.querySelectorAll('.js-ai-score').forEach(btn => {
      btn.addEventListener('click', () => this._aiScoreCompany(parseInt(btn.dataset.cid), btn));
    });
    this.root.querySelectorAll('.js-reasoning-toggle').forEach(btn => {
      btn.addEventListener('click', () => this._toggleReasoning(btn));
    });
  }

  _renderDeriskingStage(stage, hasCompare) {
    const stageColor = {
      'Pre-Seed': '#94a3b8', 'Seed': '#16a34a', 'Seed+': '#16a34a', 'Seed Extension': '#16a34a',
      'A': '#0ea5e9', 'Series A': '#0ea5e9', 'A+': '#0ea5e9',
      'B': '#7c3aed', 'Series B': '#7c3aed', 'B+': '#7c3aed',
      'C': '#db2777', 'Series C': '#db2777', 'D': '#db2777', 'Growth': '#f97316',
      'Unstaged': '#9ca3af',
    }[stage.stage] || '#9ca3af';

    const headerCols = `
      <th>Company</th>
      <th>Fund</th>
      <th title="Sector">Sector</th>
      <th class="text-center" title="${this.esc(this.derisking.compare || '')}">${this.esc(this.derisking.compare || 'Prior')}</th>
      <th class="text-center" title="${this.esc(this.derisking.period || '')}">${this.esc(this.derisking.period || 'Current')}</th>
      ${hasCompare ? '<th class="text-center" title="Quartile change since prior period">Δ</th>' : ''}
      <th>Source</th>
      <th>Action</th>`;

    const rowsHtml = stage.rows.map(r => this._renderDeriskingRow(r, hasCompare)).join('');

    return `
      <div class="pr-card mb-6">
        <div class="px-5 py-3 border-b border-gray-200 flex items-center bg-gray-50">
          <span class="inline-block w-2 h-6 rounded mr-2" style="background:${stageColor}"></span>
          <div class="font-semibold text-gray-900">${this.esc(stage.stage)}</div>
          <div class="ml-auto text-xs text-gray-500">${stage.n} compan${stage.n === 1 ? 'y' : 'ies'}</div>
        </div>
        <div class="overflow-x-auto">
          <table class="pr-table text-sm">
            <thead><tr>${headerCols}</tr></thead>
            <tbody>${rowsHtml || `<tr><td colspan="8" class="text-gray-400 p-4 text-center">No companies in this stage.</td></tr>`}</tbody>
          </table>
        </div>
      </div>`;
  }

  _quartileChip(q, total, isExited, evaluator) {
    if (isExited) {
      return `<span class="inline-block px-2 py-1 rounded text-xs font-bold bg-gray-200 text-gray-600" title="Exited">EXIT</span>`;
    }
    if (q == null) return `<span class="text-gray-300 text-xs">—</span>`;
    const colors = {
      1: 'bg-red-100 text-red-800 border-red-300',
      2: 'bg-orange-100 text-orange-700 border-orange-300',
      3: 'bg-green-100 text-green-700 border-green-300',
      4: 'bg-blue-100 text-blue-700 border-blue-300',
    };
    const totalStr = (total != null) ? `${total >= 0 ? '+' : ''}${total}` : '';
    const evalBadge = evaluator === 'llm' ? `<span class="ml-1 text-[9px] uppercase font-semibold text-purple-700">AI</span>` : '';
    return `
      <span class="inline-flex items-center justify-center px-2 py-1 rounded text-xs font-bold border ${colors[q] || 'bg-gray-100 text-gray-700'}">
        Q${q}${totalStr ? `<span class="ml-1 font-normal opacity-70">${totalStr}</span>` : ''}${evalBadge}
      </span>`;
  }

  _deltaChip(delta) {
    if (delta == null) return `<span class="text-gray-300 text-xs">—</span>`;
    if (delta === 0)   return `<span class="text-xs text-gray-500">·</span>`;
    if (delta > 0)     return `<span class="text-xs font-bold text-green-700">↑${delta}</span>`;
    return `<span class="text-xs font-bold text-red-700">↓${Math.abs(delta)}</span>`;
  }

  _renderDeriskingRow(r, hasCompare) {
    const p = r.primary;
    const c = r.compare;
    const cid = r.company_id;
    const hasReasoning = p && p.evaluator === 'llm';
    const sourceBadge = !p
      ? `<span class="text-xs text-gray-400">no score</span>`
      : (p.evaluator === 'llm'
          ? `<span class="text-xs text-purple-700" title="${this.esc(p.model_used || '')}">AI · ${this.esc((p.model_used || '').replace(/^claude-/,'').replace(/^qwen/,'qwen'))}</span>`
          : `<span class="text-xs text-gray-600">workbook</span>`);

    const expandRow = hasReasoning ? `
      <tr class="js-reasoning-row hidden" data-cid="${cid}">
        <td colspan="${hasCompare ? 8 : 7}" class="bg-purple-50 px-5 py-4">
          <div class="text-xs text-gray-500 mb-2">Loading reasoning…</div>
        </td>
      </tr>` : '';

    return `
      <tr data-cid="${cid}">
        <td>
          <a class="pr-link font-medium" href="/portfolio-review/company/${cid}">${this.esc(r.company_name)}</a>
          ${r.commercial_status ? `<div class="text-xs text-gray-500">${this.esc(r.commercial_status)}</div>` : ''}
        </td>
        <td class="text-xs text-gray-600">${this.esc(r.fund) || '—'}</td>
        <td class="text-xs text-gray-600">${this.esc(r.sector) || '—'}</td>
        <td class="text-center">${c ? this._quartileChip(c.quartile, c.total, false, c.evaluator) : '<span class="text-gray-300 text-xs">—</span>'}</td>
        <td class="text-center">${p ? this._quartileChip(p.quartile, p.total, p.is_exited, p.evaluator) : '<span class="text-gray-300 text-xs">—</span>'}</td>
        ${hasCompare ? `<td class="text-center">${this._deltaChip(r.delta_quartile)}</td>` : ''}
        <td>${sourceBadge}</td>
        <td>
          <button class="text-xs px-2 py-1 rounded bg-purple-600 text-white hover:bg-purple-700 js-ai-score" data-cid="${cid}">AI Score</button>
          ${hasReasoning ? `<button class="text-xs ml-1 px-2 py-1 rounded border border-gray-300 hover:bg-gray-50 js-reasoning-toggle" data-cid="${cid}">Why?</button>` : ''}
        </td>
      </tr>
      ${expandRow}`;
  }

  async _aiScoreCompany(cid, btn) {
    const provider = (this.derisking && this.derisking.provider) || 'anthropic';
    const period = this.derisking && this.derisking.period && this.derisking.period.endsWith(' LLM')
      ? this.derisking.period
      : `${(new Date()).getFullYear()} LLM`;
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Scoring…';
    btn.classList.add('opacity-60');
    try {
      const t = localStorage.getItem('rvm_token') || '';
      const url = `/api/portfolio-review/derisking/llm-score/${cid}?provider=${encodeURIComponent(provider)}&period=${encodeURIComponent(period)}`;
      const r = await fetch(url, { method: 'POST', headers: { 'Authorization': 'Bearer ' + t } });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        alert(`AI scoring failed: ${data.detail || r.statusText}`);
        return;
      }
      // Refresh the section so the new score appears
      await this.renderDerisking({ period });
    } catch (e) {
      alert(`AI scoring failed: ${e.message}`);
    } finally {
      btn.disabled = false;
      btn.textContent = original;
      btn.classList.remove('opacity-60');
    }
  }

  async _toggleReasoning(btn) {
    const cid = btn.dataset.cid;
    const row = this.root.querySelector(`tr.js-reasoning-row[data-cid="${cid}"]`);
    if (!row) return;
    if (!row.classList.contains('hidden')) {
      row.classList.add('hidden');
      btn.textContent = 'Why?';
      return;
    }
    row.classList.remove('hidden');
    btn.textContent = 'Hide';
    if (row.dataset.loaded === '1') return;
    try {
      const period = this.derisking.period;
      const data = await this.fetchJson(`/api/portfolio-review/derisking/${cid}${period ? `?period=${encodeURIComponent(period)}` : ''}`);
      const dims = [
        ['rapid_innovation_adopt', 'Rapid innovation & adoption'],
        ['business_model', 'Business model'],
        ['technology', 'Technology'],
        ['incentive_management', 'Incentive management'],
        ['team', 'Team'],
        ['product_growth', 'Product & growth'],
        ['ip_and_data', 'IP & Data'],
      ];
      const dimRow = (key, label) => {
        const r = (data.reasoning && data.reasoning[key]) || {};
        const score = r.score;
        const sym = score === 1 ? '+1' : score === -1 ? '−1' : score === 0 ? '0' : '—';
        const cls = score === 1 ? 'bg-green-500 text-white'
                  : score === -1 ? 'bg-red-500 text-white'
                  : score === 0 ? 'bg-yellow-300 text-gray-800'
                  : 'bg-gray-200 text-gray-500';
        const evidence = (r.evidence || []).map(e => `<li class="text-xs text-gray-600">${this.esc(e)}</li>`).join('');
        return `
          <tr>
            <td class="py-2 align-top w-48"><span class="font-medium text-gray-800">${label}</span>
              ${r.confidence ? `<div class="text-[10px] text-gray-400">conf: ${this.esc(r.confidence)}</div>` : ''}
            </td>
            <td class="py-2 align-top w-12 text-center"><span class="inline-block w-7 h-7 rounded-full text-sm leading-7 font-bold text-center ${cls}">${sym}</span></td>
            <td class="py-2 align-top">
              <div class="text-sm text-gray-800">${this.esc(r.reasoning || '—')}</div>
              ${evidence ? `<ul class="mt-1 list-disc ml-5">${evidence}</ul>` : ''}
            </td>
          </tr>`;
      };
      const header = `
        <div class="text-xs text-gray-700 mb-3">
          <span class="font-semibold">${this.esc(data.period || '')}</span>
          ${data.model_used ? `<span class="ml-2 text-purple-700">${this.esc(data.model_used)}</span>` : ''}
          ${data.confidence ? `<span class="ml-2 px-1.5 py-0.5 bg-white rounded text-[10px]">overall conf: ${this.esc(data.confidence)}</span>` : ''}
          ${data.scored_at ? `<span class="ml-2 text-gray-500">scored ${this.esc(data.scored_at)}</span>` : ''}
        </div>
        ${data.evidence_summary ? `<div class="text-sm text-gray-700 mb-3 italic">${this.esc(data.evidence_summary)}</div>` : ''}`;
      const sourceList = (data.source_files || []).map(s => `<span class="px-1.5 py-0.5 bg-white rounded text-[10px] mr-1 mb-1 inline-block">${this.esc(s.name || s.title || s.source || '')}</span>`).join('');
      row.firstElementChild.innerHTML = `
        ${header}
        <table class="w-full">${dims.map(([k, l]) => dimRow(k, l)).join('')}</table>
        ${sourceList ? `<div class="mt-3 text-[10px] text-gray-500"><div class="mb-1">Evidence base:</div>${sourceList}</div>` : ''}
      `;
      row.dataset.loaded = '1';
    } catch (e) {
      row.firstElementChild.innerHTML = `<div class="text-sm text-red-600">${this.esc(e.message)}</div>`;
    }
  }

  renderPlaceholder(label, slug) {
    this.setHTML(`
      <div class="p-8 text-center">
        <div class="inline-block px-4 py-1 rounded-full bg-gray-100 text-gray-600 text-xs font-medium">Coming soon</div>
        <div class="mt-3 text-gray-700">${this.esc(label)}</div>
        <div class="mt-1 text-xs text-gray-500">Section slug: <code>${this.esc(slug)}</code></div>
      </div>`);
  }
}
