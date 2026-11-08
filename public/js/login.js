(function () {
  const $ = (id) => document.getElementById(id);
  // only allow same-site relative redirects (no open redirect via ?next=//evil.com)
  const raw = new URLSearchParams(location.search).get('next') || '/authority.html';
  const next = /^\/(?!\/)[\w\-./]*$/.test(raw) ? raw : '/authority.html';

  fetch('/api/auth/me').then((r) => { if (r.ok) location.replace(next); }).catch(() => {});

  $('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    $('err').textContent = '';
    const btn = $('submit');
    btn.disabled = true; btn.textContent = 'Signing in…';
    try {
      const r = await fetch('/api/auth/login', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ email: $('email').value, password: $('password').value }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(r.status === 404 ? 'This server has no sign-in (use the FastAPI backend).' : j.error || 'Sign-in failed');
      location.replace(next);
    } catch (err) {
      $('err').textContent = err.message;
      $('password').value = '';
      $('password').focus();
    } finally {
      btn.disabled = false; btn.textContent = 'Sign in';
    }
  });
  $('email').focus();
})();
