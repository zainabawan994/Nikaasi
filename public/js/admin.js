(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const when = (iso) => (iso ? new Date(iso).toLocaleString() : '—');
  let me = null;

  async function call(method, path, body) {
    const r = await fetch(path, { method, headers: body ? { 'content-type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
    if (r.status === 401) { location.replace('/login.html?next=/admin.html'); throw new Error('Signed out'); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || `Request failed (${r.status})`);
    return j;
  }
  const say = (id, text, ok) => { $(id).textContent = text; $(id).style.color = ok ? 'var(--ok)' : 'var(--sev-critical)'; };

  call('GET', '/api/auth/me').then((u) => {
    me = u;
    if (me.role !== 'admin') { location.replace('/authority.html'); return; }
    $('user-chip').textContent = `${me.name} · ${me.role}`;
    $('app').classList.remove('hidden');
    loadUsers(); loadAudit(); loadDb();
  }).catch(() => {});

  $('logout').onclick = async () => { await fetch('/api/auth/logout', { method: 'POST' }).catch(() => {}); location.replace('/login.html'); };

  async function loadUsers() {
    const users = await call('GET', '/api/users');
    $('users').innerHTML = '<tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>Last sign-in</th><th></th></tr>' + users.map((u) => `
      <tr data-id="${u.id}">
        <td>${esc(u.name)}${u.id === me.id ? ' <span class="chip">you</span>' : ''}</td>
        <td>${esc(u.email)}</td>
        <td><select data-act="role" aria-label="Role for ${esc(u.email)}"><option value="authority" ${u.role === 'authority' ? 'selected' : ''}>authority</option><option value="admin" ${u.role === 'admin' ? 'selected' : ''}>admin</option></select></td>
        <td>${u.active ? '<span class="status done">active</span>' : '<span class="status">disabled</span>'}</td>
        <td>${when(u.last_login_at)}</td>
        <td style="white-space:nowrap">
          <button class="btn" data-act="toggle">${u.active ? 'Disable' : 'Enable'}</button>
          <button class="btn" data-act="reset">Reset password</button>
        </td>
      </tr>`).join('');
  }

  $('users').addEventListener('change', async (e) => {
    if (e.target.dataset.act !== 'role') return;
    const id = Number(e.target.closest('tr').dataset.id);
    try { await call('PATCH', `/api/users/${id}`, { role: e.target.value }); say('users-msg', 'Role updated. That user was signed out.', true); } catch (err) { say('users-msg', err.message); }
    loadUsers(); loadAudit();
  });

  $('users').addEventListener('click', async (e) => {
    const act = e.target.dataset.act;
    if (!act || act === 'role') return;
    const tr = e.target.closest('tr');
    const id = Number(tr.dataset.id);
    try {
      if (act === 'toggle') {
        const enable = e.target.textContent === 'Enable';
        if (!enable && !confirm('Disable this user? They will be signed out immediately.')) return;
        await call('PATCH', `/api/users/${id}`, { active: enable });
        say('users-msg', enable ? 'User enabled.' : 'User disabled and signed out.', true);
      } else if (act === 'reset') {
        const pw = prompt('New temporary password (10+ characters, letters and a number or symbol):');
        if (!pw) return;
        await call('PATCH', `/api/users/${id}`, { password: pw });
        say('users-msg', 'Password reset. Share it privately; the user was signed out.', true);
      }
    } catch (err) { say('users-msg', err.message); }
    loadUsers(); loadAudit();
  });

  $('create-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      const u = await call('POST', '/api/users', { email: $('c-email').value, name: $('c-name').value, role: $('c-role').value, password: $('c-pw').value });
      say('c-msg', `Created ${u.email}.`, true);
      $('create-form').reset();
      loadUsers(); loadAudit();
    } catch (err) { say('c-msg', err.message); }
  });

  $('pw-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      await call('POST', '/api/auth/password', { current: $('p-cur').value, new: $('p-new').value });
      say('p-msg', 'Password updated. Other sessions were signed out.', true);
      $('pw-form').reset();
      loadAudit();
    } catch (err) { say('p-msg', err.message); }
  });

  async function loadAudit() {
    const rows = await call('GET', '/api/audit?limit=200');
    $('audit').innerHTML = '<tr><th>When</th><th>Who</th><th>Action</th><th>Detail</th><th>IP</th></tr>' + rows.map((a) => `
      <tr><td style="white-space:nowrap">${when(a.at)}</td><td>${esc(a.email || '—')}</td><td><span class="chip">${esc(a.action)}</span></td>
      <td class="small">${esc(a.detail ? JSON.stringify(a.detail) : '')}</td><td>${esc(a.ip || '')}</td></tr>`).join('');
  }

  async function loadDb() {
    try {
      const s = await call('GET', '/api/db/status');
      $('db').innerHTML = s.enabled
        ? `<b style="color:var(--ok)">Connected to PostgreSQL</b><table class="tbl" style="margin-top:6px">${Object.entries(s.counts).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v}</td></tr>`).join('')}</table>`
        : `<b style="color:var(--sev-high)">Not connected</b><br>${s.error ? 'Connection failed, see server log.' : 'DATABASE_URL is not set; data is kept in memory.'}`;
    } catch (err) { $('db').textContent = err.message; }
  }
})();
