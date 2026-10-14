const money = n => new Intl.NumberFormat('en-GB', { style: 'currency', currency: 'GBP' }).format(n);
const esc = (s='') => String(s).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const passwordInput = document.querySelector('#admin-pin');
const message = document.querySelector('#admin-message');
const $ = selector => document.querySelector(selector);
const statuses = { pending: 'Pending', confirmed: 'Confirmed', declined: 'Denied', cancelled: 'Cancelled' };
let authenticated = false;
let bookings = [];
let editing = null;
let deleting = null;
let selectedServices = [];
let catalogue = null;
let busy = false;

function setAuthenticated(value) {
  authenticated = value;
  document.querySelector('#login-form').hidden = value;
  document.querySelector('#logout').hidden = !value;
  document.querySelector('#load-bookings').hidden = !value;
  document.querySelector('#export-accepted').disabled = !value;
  $('#booking-controls').hidden = !value;
  $('#export-panel').hidden = !value;
  if (!value) {
    bookings = [];
    editing = null;
    deleting = null;
    selectedServices = [];
    $('#edit-dialog').close();
    $('#delete-dialog').close();
    $('#edit-form').reset();
    $('#delete-form').reset();
    $('#edit-services').replaceChildren();
    $('#edit-reference').textContent = '';
    $('#delete-description').textContent = '';
    $('#status-filter').value = 'pending';
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

document.querySelector('#load-bookings').addEventListener('click', () => loadBookings());
$('#status-filter').addEventListener('change', render);
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

async function loadBookings(successMessage = '') {
  message.textContent = 'Loading…';
  try {
    const response = await api('/api/admin/bookings');
    const data = await response.json();
    bookings = data.bookings;
    setAuthenticated(true);
    render();
    message.textContent = successMessage;
  } catch (error) {
    bookings = [];
    document.querySelector('#admin-list').replaceChildren();
    message.textContent = error.status === 401 ? 'Sign in to view bookings.' : error.message;
  }
}

function dateTime(value) {
  return value ? new Date(value).toLocaleString('en-GB', { timeZone: 'Europe/London' }) : 'Unknown';
}

function render() {
  const filter = $('#status-filter').value;
  const matches = (b, choice) => choice === 'all' || (choice === 'closed'
    ? ['declined', 'cancelled'].includes(b.status) : b.status === choice);
  const labels = { pending: 'Pending', confirmed: 'Confirmed', closed: 'Denied / Cancelled', all: 'All requests' };
  for (const option of $('#status-filter').options) {
    option.textContent = `${labels[option.value]} (${bookings.filter(b => matches(b, option.value)).length})`;
  }
  const visible = bookings.filter(b => matches(b, filter)).sort((a, b) =>
    (Date.parse(b.createdAt) || 0) - (Date.parse(a.createdAt) || 0));
  $('#booking-count').textContent = `${visible.length} request${visible.length === 1 ? '' : 's'}`;
  const root = $('#admin-list');
  root.replaceChildren();
  if (!visible.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-state';
    empty.textContent = `No ${filter === 'all' ? '' : labels[filter].toLowerCase() + ' '}requests.`;
    root.append(empty);
    return;
  }
  visible.forEach(b => {
    const card = document.createElement('article');
    card.className = 'admin-booking';
    const services = b.services.map(s => `<li>${esc(s.name)}${s.variant ? ` — ${esc(s.variant)}` : ''} — <strong>${money(s.price)}</strong></li>`).join('');
    const isClosed = ['declined', 'cancelled'].includes(b.status);
    const retentionText = b.retentionHold
      ? `Kept for unresolved issue: ${esc(b.holdReason || '')}`
      : b.deleteAfter ? `Automatic removal after ${esc(dateTime(b.deleteAfter))}` : 'Removal date needs review. Check this request’s date and status.';
    card.innerHTML = `
      <header><h3>${esc(b.name)}</h3><span class="status ${esc(b.status)}">${esc(statuses[b.status] || b.status)}</span><small>${esc(b.id)}</small></header>
      <div class="booking-meta">
        <div class="meta-box"><small>Appointment</small><strong>${esc(b.date)} at ${esc(b.time)}</strong></div>
        <div class="meta-box"><small>Total</small><strong>${money(b.total)}</strong></div>
      </div>
      <p><strong>Email:</strong> ${esc(b.email)}<br><strong>Phone:</strong> ${esc(b.phone)}</p>
      <ul>${services}</ul>
      ${b.notes ? `<p class="preserve-lines"><strong>Customer notes:</strong> ${esc(b.notes)}</p>` : ''}
      ${b.internalNote ? `<p class="preserve-lines"><strong>Internal note:</strong> ${esc(b.internalNote)}</p>` : ''}
      ${b.studioNote ? `<p class="preserve-lines"><strong>Customer message:</strong> ${esc(b.studioNote)}</p>` : ''}
      <p class="request-dates">Received: ${esc(dateTime(b.createdAt))}<br>${retentionText}</p>
      <div class="admin-actions">
        <button class="button secondary edit" type="button">Edit / reschedule</button>
        ${isClosed ? '<button class="button ghost reopen" type="button">Reopen</button>' : ''}
        ${b.status === 'pending' ? '<button class="button ghost confirm" type="button">Confirm</button>' : ''}
        ${!isClosed ? `<button class="button ghost decline" type="button">${b.status === 'confirmed' ? 'Cancel appointment' : 'Decline request'}</button>` : ''}
        <button class="button danger-outline delete" type="button" ${b.retentionHold ? 'disabled title="Remove the unresolved-issue hold before deleting"' : ''}>Delete</button>
      </div>`;
    card.querySelector('.edit').addEventListener('click', () => openEditor(b, b.status));
    card.querySelector('.reopen')?.addEventListener('click', () => openEditor(b, 'pending'));
    card.querySelector('.confirm')?.addEventListener('click', () => openEditor(b, 'confirmed'));
    card.querySelector('.decline')?.addEventListener('click', () => openEditor(b, b.status === 'confirmed' ? 'cancelled' : 'declined'));
    card.querySelector('.delete').addEventListener('click', () => openDelete(b));
    root.appendChild(card);
  });
}

async function loadCatalogue() {
  if (catalogue) return;
  const response = await fetch('/services.json');
  if (!response.ok) throw new Error('The treatment list could not load. Try again.');
  const categories = await response.json();
  catalogue = categories.flatMap(category => category.items.flatMap(item =>
    item.variants?.length ? item.variants.map(variant => ({ category: category.category,
      name: item.name, variant: variant.label, price: variant.price }))
      : [{ category: category.category, name: item.name, variant: '', price: item.price }]));
  for (const [index, service] of catalogue.entries()) {
    const option = document.createElement('option');
    option.value = index;
    option.textContent = `${service.category} — ${service.name}${service.variant ? ' — ' + service.variant : ''} (${money(service.price)})`;
    $('#add-treatment').append(option);
  }
}

async function openEditor(booking, status) {
  if (busy || $('#edit-dialog').open) return;
  try {
    await loadCatalogue();
    if (!authenticated || $('#edit-dialog').open) return;
    editing = booking;
    $('#edit-form').reset();
    $('#edit-error').textContent = '';
    $('#edit-reference').textContent = booking.id;
    $('#edit-title').textContent = status === 'declined' ? 'Decline request' : status === 'cancelled' ? 'Cancel appointment' : 'Edit request';
    for (const key of ['name', 'email', 'phone', 'date', 'time', 'notes']) $(`#edit-${key}`).value = booking[key] || '';
    $('#edit-status').value = status;
    $('#edit-internal-note').value = booking.internalNote || '';
    $('#edit-hold').checked = Boolean(booking.retentionHold);
    $('#edit-hold-reason').value = booking.holdReason || '';
    selectedServices = booking.services.map(service => ({ ...service }));
    renderServices();
    updateEditorFields();
    $('#edit-dialog').showModal();
  } catch (error) { message.textContent = error.message; }
}

function renderServices() {
  $('#edit-services').replaceChildren();
  selectedServices.forEach((service, index) => {
    const row = document.createElement('li');
    row.innerHTML = `<span>${esc(service.name)}${service.variant ? ' — ' + esc(service.variant) : ''} — ${money(service.price)}</span><button class="button ghost" type="button" aria-label="Remove ${esc(service.name)}">Remove</button>`;
    row.querySelector('button').addEventListener('click', () => { selectedServices.splice(index, 1); renderServices(); });
    $('#edit-services').append(row);
  });
  $('#edit-total').textContent = money(selectedServices.reduce((total, service) => total + Number(service.price), 0));
}

$('#add-treatment-button').addEventListener('click', () => {
  const index = $('#add-treatment').value;
  if (index === '' || !catalogue) return;
  const service = catalogue[Number(index)];
  if (!selectedServices.some(s => s.category === service.category && s.name === service.name && s.variant === service.variant)) {
    if (selectedServices.length >= 20) { $('#edit-error').textContent = 'Choose no more than twenty treatments.'; return; }
    selectedServices.push({ ...service });
    renderServices();
  }
  $('#add-treatment').value = '';
});

function updateEditorFields() {
  const closed = ['declined', 'cancelled'].includes($('#edit-status').value);
  $('#edit-internal-note').required = closed;
  $('#internal-note-label').textContent = closed ? 'Internal reason (required)' : 'Internal note';
  $('#hold-reason-label').hidden = !$('#edit-hold').checked;
  $('#edit-hold-reason').required = $('#edit-hold').checked;
  $('#release-hold-warning').hidden = !editing?.retentionHold || $('#edit-hold').checked;
  $('#customer-message-label').hidden = !$('#edit-notify').checked;
  $('#save-booking').textContent = $('#edit-notify').checked ? 'Save and email customer' : 'Save changes';
  const date = new Date(`${$('#edit-date').value}T12:00:00`);
  $('#edit-time').min = date.getDay() === 0 ? '11:00' : '10:00';
}
for (const id of ['edit-status', 'edit-hold', 'edit-notify', 'edit-date']) $(`#${id}`).addEventListener('change', updateEditorFields);

function setBusy(value) {
  busy = value;
  $('#edit-fields').disabled = value;
  for (const button of document.querySelectorAll('dialog button')) button.disabled = value;
  $('#confirm-delete').disabled = value;
}
for (const dialog of document.querySelectorAll('dialog')) {
  dialog.querySelectorAll('.close-dialog').forEach(button => button.addEventListener('click', () => dialog.close()));
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
}

$('#edit-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!editing || busy) return;
  if (!selectedServices.length) { $('#edit-error').textContent = 'Choose at least one treatment.'; return; }
  const payload = { expectedRevision: editing.revision, status: $('#edit-status').value,
    services: selectedServices, internalNote: $('#edit-internal-note').value,
    retentionHold: $('#edit-hold').checked, holdReason: $('#edit-hold-reason').value,
    notifyCustomer: $('#edit-notify').checked, customerMessage: $('#edit-customer-message').value };
  for (const key of ['name', 'email', 'phone', 'date', 'time', 'notes']) payload[key] = $(`#edit-${key}`).value;
  setBusy(true);
  $('#edit-error').textContent = '';
  try {
    const response = await api(`/api/admin/bookings/${encodeURIComponent(editing.id)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
    });
    const data = await response.json();
    $('#edit-dialog').close();
    let outcome = 'Booking saved. No customer email sent.';
    if (data.notificationRequested) outcome = data.emailSent ? 'Booking saved. Customer email sent.' : 'Booking saved, but the customer email failed. Please contact them directly.';
    await loadBookings(outcome);
  } catch (error) { $('#edit-error').textContent = error.message; message.textContent = error.message; }
  finally { setBusy(false); }
});

function openDelete(booking) {
  deleting = booking;
  $('#delete-form').reset();
  $('#delete-error').textContent = '';
  $('#delete-description').textContent = `${booking.name} — ${booking.date} at ${booking.time} (${statuses[booking.status]}).`;
  $('#delete-active-warning').hidden = !['pending', 'confirmed'].includes(booking.status);
  $('#delete-dialog').showModal();
}

$('#delete-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!deleting || busy || !$('#confirm-delete').checked) return;
  setBusy(true);
  $('#delete-error').textContent = '';
  try {
    await api(`/api/admin/bookings/${encodeURIComponent(deleting.id)}`, {
      method: 'DELETE', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirmDelete: true, expectedRevision: deleting.revision })
    });
    $('#delete-dialog').close();
    await loadBookings('Request permanently deleted.');
  } catch (error) { $('#delete-error').textContent = error.message; message.textContent = error.message; }
  finally { setBusy(false); }
});

setAuthenticated(false);
loadBookings();
