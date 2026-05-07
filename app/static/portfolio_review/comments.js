/**
 * Comments widget — used on the index, section pages, and company detail.
 *
 *   new CommentsWidget(rootEl, entityType, entityKey)
 *
 *   entityType: 'company' | 'section' | 'investment' | 'metric'
 *   entityKey:  the company id (as string), section slug, or composite key.
 *
 * Renders an inline thread with a textarea at the bottom. Polls every
 * 30s when the tab is visible to pick up other users' comments.
 */
class CommentsWidget {
  constructor(rootEl, entityType, entityKey) {
    this.root = rootEl;
    this.entityType = entityType;
    this.entityKey = entityKey;
    this.comments = [];
    this.render();
    this.refresh();
    this._poll = setInterval(() => {
      if (!document.hidden) this.refresh();
    }, 30000);
  }

  _authHeaders(extra = {}) {
    const token = localStorage.getItem('rvm_token') || '';
    const h = { ...extra };
    if (token) h['Authorization'] = 'Bearer ' + token;
    return h;
  }

  async refresh() {
    try {
      const r = await fetch(
        `/api/portfolio-review/comments?entity_type=${encodeURIComponent(this.entityType)}` +
        `&entity_key=${encodeURIComponent(this.entityKey)}`,
        { headers: this._authHeaders() }
      );
      if (!r.ok) {
        if (r.status === 401) {
          this.renderSignedOut();
          return;
        }
        throw new Error(r.statusText);
      }
      const data = await r.json();
      this.comments = data.comments || [];
      this.render();
    } catch (e) {
      console.warn('Comment refresh failed:', e);
    }
  }

  async post(body) {
    const r = await fetch('/api/portfolio-review/comments', {
      method: 'POST',
      headers: this._authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({
        entity_type: this.entityType,
        entity_key: this.entityKey,
        body,
      }),
    });
    if (r.ok) {
      this.refresh();
    } else if (r.status === 401) {
      alert('Please sign in to comment.');
    } else {
      const err = await r.json().catch(() => ({}));
      alert('Failed to post comment: ' + (err.detail || r.statusText));
    }
  }

  async deleteComment(id) {
    if (!confirm('Delete this comment?')) return;
    const r = await fetch(`/api/portfolio-review/comments/${id}`, {
      method: 'DELETE',
      headers: this._authHeaders(),
    });
    if (r.ok) this.refresh();
  }

  renderSignedOut() {
    this.root.innerHTML = `
      <div class="text-sm text-gray-500">
        <a class="pr-link" href="/">Sign in</a> to view and post comments.
      </div>`;
  }

  render() {
    const escape = (s) => (s || '').replace(/[&<>"']/g, (c) => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[c]));

    const list = this.comments.length
      ? this.comments.map(c => `
          <div class="text-sm py-2 border-b border-gray-100 last:border-0" data-id="${c.id}">
            <div class="flex items-baseline gap-2">
              <span class="font-semibold text-gray-900">${escape(c.user_username || 'unknown')}</span>
              <span class="text-xs text-gray-400">${escape(c.created_at)}</span>
              <button class="ml-auto text-xs text-gray-400 hover:text-red-600 js-delete">delete</button>
            </div>
            <div class="text-gray-700 mt-1 whitespace-pre-wrap">${escape(c.body)}</div>
          </div>
        `).join('')
      : `<div class="text-sm text-gray-400 py-3">No comments yet.</div>`;

    this.root.innerHTML = `
      <div class="text-sm font-semibold text-gray-700 mb-2">Comments</div>
      <div class="js-list">${list}</div>
      <form class="mt-3 js-form">
        <textarea
          class="w-full border border-gray-200 rounded p-2 text-sm focus:outline-none focus:border-blue-500"
          rows="2" placeholder="Add a comment…" required></textarea>
        <div class="flex justify-end mt-2">
          <button type="submit" class="text-xs px-3 py-1.5 rounded bg-blue-600 text-white hover:bg-blue-700">Post</button>
        </div>
      </form>
    `;

    const form = this.root.querySelector('.js-form');
    form?.addEventListener('submit', (e) => {
      e.preventDefault();
      const ta = form.querySelector('textarea');
      const body = ta.value.trim();
      if (!body) return;
      ta.value = '';
      this.post(body);
    });

    this.root.querySelectorAll('.js-delete').forEach(btn => {
      btn.addEventListener('click', () => {
        const id = btn.closest('[data-id]').dataset.id;
        this.deleteComment(id);
      });
    });
  }
}
