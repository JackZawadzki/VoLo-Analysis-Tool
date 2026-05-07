/**
 * Direct workbook upload card — drop-zone for .xlsx/.xlsm uploads.
 *
 * Posts a multipart/form-data request to /api/portfolio-review/import-upload,
 * which writes the file to a tempfile and runs loader.run_import() under the
 * hood. Mirrors the Drive sync result UX (success / partial / failed badge).
 */
class UploadImportCard {
  constructor(rootEl) {
    this.root = rootEl;
    this.dropzone = rootEl.querySelector('#upload-dropzone');
    this.fileInput = rootEl.querySelector('#upload-file-input');
    this.defaultMsg = rootEl.querySelector('#upload-dropzone-default');
    this.activeMsg = rootEl.querySelector('#upload-dropzone-active');
    this.busyMsg = rootEl.querySelector('#upload-dropzone-busy');
    this.statusBadge = rootEl.querySelector('#upload-status-badge');
    this.resultEl = rootEl.querySelector('#upload-result');
    this.busy = false;
    this._wire();
  }

  authHeaders(extra = {}) {
    const t = localStorage.getItem('rvm_token') || '';
    const h = { ...extra };
    if (t) h['Authorization'] = 'Bearer ' + t;
    return h;
  }

  _wire() {
    this.dropzone.addEventListener('click', () => {
      if (!this.busy) this.fileInput.click();
    });
    this.fileInput.addEventListener('change', (e) => {
      const f = e.target.files && e.target.files[0];
      if (f) this._upload(f);
      this.fileInput.value = '';
    });

    ['dragenter', 'dragover'].forEach((evt) => {
      this.dropzone.addEventListener(evt, (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (this.busy) return;
        this.defaultMsg.classList.add('hidden');
        this.activeMsg.classList.remove('hidden');
      });
    });
    ['dragleave', 'drop'].forEach((evt) => {
      this.dropzone.addEventListener(evt, (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (this.busy) return;
        this.activeMsg.classList.add('hidden');
        this.defaultMsg.classList.remove('hidden');
      });
    });
    this.dropzone.addEventListener('drop', (e) => {
      if (this.busy) return;
      const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) this._upload(f);
    });
  }

  _setBusy(busy) {
    this.busy = busy;
    if (busy) {
      this.defaultMsg.classList.add('hidden');
      this.activeMsg.classList.add('hidden');
      this.busyMsg.classList.remove('hidden');
      this.dropzone.classList.add('opacity-60', 'cursor-not-allowed');
    } else {
      this.defaultMsg.classList.remove('hidden');
      this.activeMsg.classList.add('hidden');
      this.busyMsg.classList.add('hidden');
      this.dropzone.classList.remove('opacity-60', 'cursor-not-allowed');
    }
  }

  _setBadge(status) {
    const cls = {
      success: 'bg-green-100 text-green-800',
      partial: 'bg-yellow-100 text-yellow-800',
      failed:  'bg-red-100 text-red-800',
    }[status] || 'bg-gray-100 text-gray-700';
    this.statusBadge.className = 'text-xs px-2 py-0.5 rounded ml-auto ' + cls;
    this.statusBadge.textContent = status || '';
  }

  async _upload(file) {
    const name = (file.name || '').toLowerCase();
    if (!name.endsWith('.xlsx') && !name.endsWith('.xlsm')) {
      this._renderError('Only .xlsx / .xlsm workbooks are supported.');
      return;
    }
    this._setBusy(true);
    this.resultEl.innerHTML = '';
    this.statusBadge.textContent = '';
    try {
      const fd = new FormData();
      fd.append('file', file, file.name);
      const r = await fetch('/api/portfolio-review/import-upload', {
        method: 'POST',
        headers: this.authHeaders(),
        body: fd,
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        this._setBadge('failed');
        this._renderError(data.detail || `Import failed (HTTP ${r.status}).`);
        return;
      }
      this._setBadge(data.status || 'success');
      this._renderResult(file.name, data);
    } catch (err) {
      this._setBadge('failed');
      this._renderError(String(err && err.message ? err.message : err));
    } finally {
      this._setBusy(false);
    }
  }

  _renderResult(filename, data) {
    const counts = data.counts || {};
    const errs = data.errors || [];
    const rows = Object.entries(counts)
      .map(([k, v]) => `<span class="px-1.5 py-0.5 bg-gray-100 rounded text-xs mr-1">${k}: <strong>${v}</strong></span>`)
      .join('');
    const errBlock = errs.length
      ? `<details class="mt-2"><summary class="text-xs text-red-700 cursor-pointer">${errs.length} warning${errs.length === 1 ? '' : 's'}</summary>
           <ul class="text-xs text-red-700 list-disc ml-5 mt-1">${errs.slice(0, 20).map(e => `<li>${this._esc(String(e))}</li>`).join('')}</ul>
         </details>`
      : '';
    this.resultEl.innerHTML = `
      <div class="text-sm">
        Imported <strong>${this._esc(filename)}</strong>
        ${data.as_of_date ? `<span class="text-gray-500">(as of ${this._esc(data.as_of_date)})</span>` : ''}
      </div>
      <div class="mt-1">${rows || '<span class="text-xs text-gray-500">No counts returned</span>'}</div>
      ${errBlock}
    `;
  }

  _renderError(msg) {
    this.resultEl.innerHTML = `<div class="text-sm text-red-700">Upload failed: ${this._esc(msg)}</div>`;
  }

  _esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }
}
