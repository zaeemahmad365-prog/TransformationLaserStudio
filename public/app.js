const money = n => new Intl.NumberFormat('en-GB', { style: 'currency', currency: 'GBP' }).format(n);
let services = [];
let selected = [];

const $ = s => document.querySelector(s);

async function init() {
  services = await fetch('services.json').then(r => r.json());
  renderServices();
  setupPicker();
  setupBookingForm();
  setupNav();
  $('#year').textContent = new Date().getFullYear();

  const dateEl = $('#booking-date');
  const today = new Date();
  const local = new Date(today.getTime() - today.getTimezoneOffset() * 60000).toISOString().slice(0,10);
  dateEl.min = local;
  dateEl.addEventListener('change', updateBookingTimeLimits);
  updateBookingTimeLimits();
}

function renderServices() {
  const root = $('#services-grid');
  root.innerHTML = '';
  services.forEach(category => {
    const wrap = document.createElement(category.compact ? 'details' : 'article');
    wrap.className = `service-card ${category.compact ? 'nails-card' : ''}`;
    if (category.compact) {
      const summary = document.createElement('summary');
      summary.innerHTML = `<h3>${escapeHtml(category.category)}</h3><p>Tap to expand the full nail price list.</p>`;
      wrap.appendChild(summary);
    } else {
      wrap.innerHTML = `<h3>${escapeHtml(category.category)}</h3>`;
    }
    const list = document.createElement('ul');
    list.className = 'service-list';
    category.items.forEach(item => {
      const li = document.createElement('li');
      const left = document.createElement('div');
      left.innerHTML = `<strong>${escapeHtml(item.name)}</strong>`;
      if (item.description) {
        left.innerHTML += `<div class="service-description">${escapeHtml(item.description)}</div>`;
      }
      if (item.variants) {
        left.innerHTML += `<div class="variant-list">${item.variants.map(v => `${escapeHtml(v.label)} ${money(v.price)}`).join(' · ')}</div>`;
      }
      const price = document.createElement('span');
      price.className = 'price';
      price.textContent = item.variants ? 'Options' : money(item.price);
      li.append(left, price);
      list.appendChild(li);
    });
    wrap.appendChild(list);
    if (category.disclaimer) {
      const disclaimer = document.createElement('p');
      disclaimer.className = 'service-disclaimer';
      disclaimer.textContent = category.disclaimer;
      wrap.appendChild(disclaimer);
    }
    root.appendChild(wrap);
  });
}

function setupPicker() {
  const categorySelect = $('#category-select');
  services.forEach((c, i) => categorySelect.add(new Option(c.category, i)));
  categorySelect.addEventListener('change', populateServices);
  $('#service-select').addEventListener('change', populateVariants);
  $('#add-service').addEventListener('click', addSelectedService);
  populateServices();
}

function populateServices() {
  const category = services[Number($('#category-select').value)];
  const serviceSelect = $('#service-select');
  serviceSelect.innerHTML = '';
  category.items.forEach((item, i) => serviceSelect.add(new Option(item.name, i)));
  populateVariants();
}

function populateVariants() {
  const category = services[Number($('#category-select').value)];
  const item = category.items[Number($('#service-select').value)];
  const wrap = $('#variant-wrap');
  const variantSelect = $('#variant-select');
  variantSelect.innerHTML = '';
  if (item?.variants?.length) {
    wrap.classList.remove('hidden');
    item.variants.forEach((v, i) => variantSelect.add(new Option(`${v.label} — ${money(v.price)}`, i)));
  } else {
    wrap.classList.add('hidden');
  }
}

function addSelectedService() {
  const ci = Number($('#category-select').value);
  const si = Number($('#service-select').value);
  const category = services[ci];
  const item = category.items[si];
  const variant = item.variants ? item.variants[Number($('#variant-select').value)] : null;
  const entry = {
    category: category.category,
    name: item.name,
    variant: variant?.label || '',
    price: variant?.price ?? item.price
  };
  const key = [entry.category, entry.name, entry.variant].join('|');
  if (selected.some(s => s.key === key)) return;
  selected.push({ ...entry, key });
  renderSelected();
}

function renderSelected() {
  const root = $('#selected-services');
  const summary = $('#summary-lines');
  if (!selected.length) {
    root.innerHTML = '<p class="empty-state">No treatments added yet.</p>';
    summary.innerHTML = '<span>No treatments selected</span>';
  } else {
    root.innerHTML = '';
    summary.innerHTML = '';
    selected.forEach((item, index) => {
      const row = document.createElement('div');
      row.className = 'selected-row';
      row.innerHTML = `<span><strong>${escapeHtml(item.name)}</strong>${item.variant ? `<br><small>${escapeHtml(item.variant)}</small>` : ''}</span><strong>${money(item.price)}</strong>`;
      const remove = document.createElement('button');
      remove.type = 'button'; remove.className = 'remove-service'; remove.setAttribute('aria-label', `Remove ${item.name}`); remove.textContent = '×';
      remove.addEventListener('click', () => { selected.splice(index, 1); renderSelected(); });
      row.appendChild(remove);
      root.appendChild(row);

      const s = document.createElement('div');
      s.className = 'summary-item';
      s.innerHTML = `<span>${escapeHtml(item.name)}${item.variant ? ` — ${escapeHtml(item.variant)}` : ''}</span><strong>${money(item.price)}</strong>`;
      summary.appendChild(s);
    });
  }
  const total = selected.reduce((n, s) => n + s.price, 0);
  $('#booking-total').textContent = money(total);
}

function updateBookingTimeLimits() {
  const dateEl = $('#booking-date');
  const timeEl = document.querySelector('input[name="time"]');
  if (!dateEl?.value || !timeEl) {
    if (timeEl) { timeEl.min = '10:00'; timeEl.max = '19:00'; }
    return;
  }
  const day = new Date(`${dateEl.value}T00:00:00`).getDay();
  timeEl.min = day === 0 ? '11:00' : '10:00';
  timeEl.max = '19:00';
}

function setupBookingForm() {
  const form = $('#booking-form');
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const message = $('#form-message');
    message.className = 'form-message'; message.textContent = '';
    if (!form.reportValidity()) return;
    if (!selected.length) {
      message.className = 'form-message error';
      message.textContent = 'Please add at least one treatment.';
      return;
    }
    const data = Object.fromEntries(new FormData(form).entries());
    const requestedDay = new Date(`${data.date}T00:00:00`).getDay();
    const openingTime = requestedDay === 0 ? '11:00' : '10:00';
    if (data.time < openingTime || data.time > '19:00') {
      message.className = 'form-message error';
      message.textContent = requestedDay === 0
        ? 'Sunday booking times are between 11am and 7pm.'
        : 'Booking times are between 10am and 7pm, Monday to Saturday.';
      return;
    }
    if (requestedDay === 0 && selected.some(s => s.category !== 'Nails')) {
      message.className = 'form-message error';
      message.textContent = 'Sundays are available for nail treatments only. Please choose Nails or select another day.';
      return;
    }
    const payload = { ...data, services: selected.map(({key, ...rest}) => rest) };
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true; button.textContent = 'Sending…';
    try {
      const res = await fetch('/api/bookings', { method: 'POST', headers: { 'Content-Type':'application/json' }, body: JSON.stringify(payload) });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error || 'Unable to send your request.');
      message.className = 'form-message success';
      message.textContent = `Request ${body.bookingId} received. We’ll review your preferred time and email you when it is confirmed.`;
      form.reset(); selected = []; renderSelected(); populateServices();
      const today = new Date();
      $('#booking-date').min = new Date(today.getTime() - today.getTimezoneOffset() * 60000).toISOString().slice(0,10);
      updateBookingTimeLimits();
    } catch (err) {
      message.className = 'form-message error';
      message.textContent = err.message;
    } finally {
      button.disabled = false; button.textContent = 'Send booking request';
    }
  });
}

function setupNav() {
  const button = $('.menu-toggle');
  const nav = $('#nav');
  button.addEventListener('click', () => {
    const open = nav.classList.toggle('open');
    button.setAttribute('aria-expanded', String(open));
  });
  nav.querySelectorAll('a').forEach(a => a.addEventListener('click', () => { nav.classList.remove('open'); button.setAttribute('aria-expanded','false'); }));
}

function escapeHtml(value='') {
  return String(value).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}

init().catch(err => {
  console.error(err);
  const msg = document.createElement('p');
  msg.textContent = 'The website could not load its service list. Please start it using the included run file rather than opening index.html directly.';
  msg.style.padding = '20px';
  document.body.prepend(msg);
});
