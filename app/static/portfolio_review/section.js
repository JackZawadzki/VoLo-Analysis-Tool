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

  renderPlaceholder(label, slug) {
    this.setHTML(`
      <div class="p-8 text-center">
        <div class="inline-block px-4 py-1 rounded-full bg-gray-100 text-gray-600 text-xs font-medium">Coming soon</div>
        <div class="mt-3 text-gray-700">${this.esc(label)}</div>
        <div class="mt-1 text-xs text-gray-500">Section slug: <code>${this.esc(slug)}</code></div>
      </div>`);
  }
}
