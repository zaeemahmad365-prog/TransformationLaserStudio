const money = n => new Intl.NumberFormat('en-GB', { style: 'currency', currency: 'GBP' }).format(n);
const esc = (s='') => String(s).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const passwordInput = document.querySelector('#admin-pin');
const message = document.querySelector('#admin-message');
let authenticated = false;

function setAuthenticated(value) {
  authenticated = value;
  document.querySelector('#login-form').hidden = value;
  document.querySelector('#logout').hidden = !value;
  document.querySelector('#load-bookings').hidden = !value;
  document.querySelector('#export-accepted').disabled = !value;
  if (!value) {
    document.querySelector('#admin-list').replaceChildren();
    document.querySelector('#export-message').textContent = '';
  }
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { 'X-CSRF-Protection': '1', ...options.headers }
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    if (response.status === 401) setAuthenticated(false);
    const fallback = response.status === 404
      ? 'The booking service could not be found. Please contact the studio owner.'
      : 'The booking service is unavailable. Please try again.';
    const error = new Error(data.error || fallback);
    error.status = response.status;
    throw error;
  }
  return response;
}

document.querySelector('#login-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = document.querySelector('#login');
  button.disabled = true;
  message.textContent = 'Signing in…';
  const password = passwordInput.value;
  passwordInput.value = '';
  try {
    await api('/api/admin/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password })
    });
    await loadBookings();
  } catch (error) {
    setAuthenticated(false);
    message.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

document.querySelector('#logout').addEventListener('click', async () => {
  try {
    await api('/api/admin/logout', { method: 'POST' });
    setAuthenticated(false);
    message.textContent = 'Signed out.';
  } catch (error) {
    message.textContent = error.message;
  }
});

document.querySelector('#load-bookings').addEventListener('click', loadBookings);
document.querySelector('#export-accepted').addEventListener('click', exportAcceptedWeek);
document.querySelector('#export-week').value = currentIsoWeek();

function currentIsoWeek() {
  const now = new Date();
  const date = new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
  const day = date.getUTCDay() || 7;
  date.setUTCDate(date.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(date.getUTCFullYear(), 0, 1));
  const week = Math.ceil((((date - yearStart) / 86400000) + 1) / 7);
  return `${date.getUTCFullYear()}-W${String(week).padStart(2, '0')}`;
}

async function exportAcceptedWeek() {
  const week = document.querySelector('#export-week').value;
  const msg = document.querySelector('#export-message');
  if (!week) { msg.textContent = 'Choose a week first.'; return; }
  if (!authenticated) { msg.textContent = 'Sign in first.'; return; }

  msg.textContent = 'Creating Excel file…';
  try {
    const r = await api(`/api/admin/accepted-bookings/export?week=${encodeURIComponent(week)}`);

    const blob = await r.blob();
    const disposition = r.headers.get('Content-Disposition') || '';
    const match = disposition.match(/filename="?([^";]+)"?/i);
    const filename = match ? match[1] : `accepted-bookings-${week}.xlsx`;
    const count = Number(r.headers.get('X-Export-Count') || 0);
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    msg.textContent = `${count} accepted booking${count === 1 ? '' : 's'} exported.`;
  } catch (e) {
    msg.textContent = e.message;
  }
}

async function loadBookings() {
  message.textContent = 'Loading…';
  try {
    const response = await api('/api/admin/bookings');
    const data = await response.json();
    setAuthenticated(true);
    render(data.bookings);
    message.textContent = `${data.bookings.length} request${data.bookings.length === 1 ? '' : 's'}`;
  } catch (error) {
    document.querySelector('#admin-list').replaceChildren();
    message.textContent = error.message;
  }
}

function render(bookings) {
  const root = document.querySelector('#admin-list');
  root.innerHTML = '';
  if (!bookings.length) { root.innerHTML = '<p>No booking requests yet.</p>'; return; }
  bookings.forEach(b => {
    const card = document.createElement('article');
    card.className = 'admin-booking';
    const services = b.services.map(s => `<li>${esc(s.name)}${s.variant ? ` — ${esc(s.variant)}` : ''} — <strong>${money(s.price)}</strong></li>`).join('');
    card.innerHTML = `
      <header><h3>${esc(b.name)}</h3><span class="status ${esc(b.status)}">${esc(b.status)}</span><small>${esc(b.id)}</small></header>
      <div class="booking-meta">
        <div class="meta-box"><small>Requested</small><strong>${esc(b.date)} at ${esc(b.time)}</strong></div>
        <div class="meta-box"><small>Total</small><strong>${money(b.total)}</strong></div>
      </div>
      <p><strong>Email:</strong> ${esc(b.email)}<br><strong>Phone:</strong> ${esc(b.phone)}</p>
      <ul>${services}</ul>
      ${b.notes ? `<p><strong>Notes:</strong> ${esc(b.notes)}</p>` : ''}
      <p><small>Received: ${new Date(b.createdAt).toLocaleString('en-GB')}</small></p>
      <div class="admin-actions">
        <textarea rows="3" placeholder="Optional message for the customer"></textarea>
        <button class="button secondary confirm">Confirm booking</button>
        <button class="button ghost decline">Decline booking</button>
      </div>`;
    card.querySelector('.confirm').addEventListener('click', () => updateBooking(b.id, 'confirmed', card.querySelector('textarea').value));
    card.querySelector('.decline').addEventListener('click', () => updateBooking(b.id, 'declined', card.querySelector('textarea').value));
    root.appendChild(card);
  });
}

async function updateBooking(id, status, note) {
  try {
    const response = await api(`/api/admin/bookings/${encodeURIComponent(id)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status, note })
    });
    const data = await response.json();
    alert(data.emailSent ? `Booking ${status}. Customer email sent.`
      : `Booking ${status}, but the customer email was not sent. Please contact the customer directly.`);
    await loadBookings();
  } catch (error) {
    message.textContent = error.message;
  }
}

setAuthenticated(false);
loadBookings();
