/* Admin: productos, pedidos y ajustes */
(function () {
  'use strict';
  var notice = document.getElementById('notice');

  function money(cents) {
    return new Intl.NumberFormat('es-HN', { style: 'currency', currency: 'HNL' }).format(cents / 100);
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function showNotice(msg, isErr) {
    notice.textContent = msg;
    notice.style.background = isErr ? '#fee2e2' : '#d1fae5';
    notice.style.borderColor = isErr ? '#dc2626' : '#059669';
    notice.style.color = isErr ? '#991b1b' : '#065f46';
    notice.classList.remove('hidden');
    setTimeout(function () { notice.classList.add('hidden'); }, 3500);
  }
  async function api(path, opts) {
    var r = await fetch(path, opts);
    if (r.status === 401) { window.location.href = '/admin/login'; throw new Error('auth'); }
    var d = await r.json().catch(function () { return {}; });
    if (!r.ok) throw new Error(d.error || 'Error en el servidor.');
    return d;
  }

  /* Tabs */
  document.querySelectorAll('.tab').forEach(function (t) {
    t.addEventListener('click', function () {
      document.querySelectorAll('.tab').forEach(function (x) { x.classList.remove('active'); });
      t.classList.add('active');
      document.querySelectorAll('.tab-panel').forEach(function (p) { p.classList.add('hidden'); });
      document.getElementById('tab-' + t.dataset.tab).classList.remove('hidden');
      if (t.dataset.tab === 'pedidos') loadOrders();
      if (t.dataset.tab === 'ajustes') loadSettings();
    });
  });

  document.getElementById('logout-btn').addEventListener('click', async function () {
    await fetch('/api/logout', { method: 'POST' });
    window.location.href = '/admin/login';
  });

  /* Productos */
  var modal = document.getElementById('product-modal');
  function openModal(p) {
    document.getElementById('modal-title').textContent = p ? 'Editar producto' : 'Nuevo producto';
    document.getElementById('p-id').value = p ? p.id : '';
    document.getElementById('p-name').value = p ? p.name : '';
    document.getElementById('p-desc').value = p ? p.description : '';
    document.getElementById('p-price').value = p ? (p.price_cents / 100).toFixed(2) : '';
    document.getElementById('p-stock').value = p ? p.stock : 0;
    document.getElementById('p-sizes').value = p ? (p.sizes || []).join(', ') : '';
    document.getElementById('p-category').value = p ? (p.category || '') : '';
    document.getElementById('p-sku').value = p ? p.sku : '';
    document.getElementById('p-photo').value = p ? p.photo : '';
    document.getElementById('p-photo-file').value = '';
    var prev = document.getElementById('p-photo-preview');
    if (p && p.photo) { prev.src = p.photo; prev.classList.remove('hidden'); }
    else { prev.classList.add('hidden'); }
    document.getElementById('p-active').checked = p ? p.active : true;
    document.getElementById('product-error').classList.add('hidden');
    modal.classList.remove('hidden');
  }
  function closeModal() { modal.classList.add('hidden'); }
  document.getElementById('modal-close').addEventListener('click', closeModal);
  modal.addEventListener('click', function (e) { if (e.target === modal) closeModal(); });
  document.getElementById('new-product-btn').addEventListener('click', function () { openModal(null); });

  document.getElementById('p-photo-file').addEventListener('change', async function (e) {
    var file = e.target.files[0];
    if (!file) return;
    var fd = new FormData();
    fd.append('file', file);
    try {
      var d = await api('/api/upload', { method: 'POST', body: fd });
      document.getElementById('p-photo').value = d.url;
      var prev = document.getElementById('p-photo-preview');
      prev.src = d.url; prev.classList.remove('hidden');
      showNotice('Foto subida ✅');
    } catch (err) { showNotice(err.message, true); }
  });

  document.getElementById('product-form').addEventListener('submit', async function (e) {
    e.preventDefault();
    var errEl = document.getElementById('product-error');
    errEl.classList.add('hidden');
    var id = document.getElementById('p-id').value;
    var body = {
      name: document.getElementById('p-name').value,
      description: document.getElementById('p-desc').value,
      price: document.getElementById('p-price').value,
      stock: document.getElementById('p-stock').value,
      sizes: document.getElementById('p-sizes').value,
      category: document.getElementById('p-category').value,
      sku: document.getElementById('p-sku').value,
      photo: document.getElementById('p-photo').value,
      active: document.getElementById('p-active').checked
    };
    try {
      if (id) await api('/api/admin/products/' + id, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      else await api('/api/admin/products', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      closeModal(); loadProducts(); showNotice('Producto guardado ✅');
    } catch (err) { errEl.textContent = err.message; errEl.classList.remove('hidden'); }
  });

  async function loadProducts() {
    var list = document.getElementById('product-list');
    try {
      var items = await api('/api/admin/products');
      if (!items.length) { list.innerHTML = '<p class="hint">No hay productos. Toca «Nuevo producto» para empezar.</p>'; return; }
      list.innerHTML = '';
      items.forEach(function (p) {
        var row = document.createElement('div');
        row.className = 'admin-row';
        row.innerHTML =
          (p.photo ? '<img class="thumb" src="' + p.photo + '" alt="">' : '<img class="thumb" alt="">') +
          '<div class="info"><strong>' + escapeHtml(p.name) + '</strong>' +
          '<small>' + money(p.price_cents) + ' · Stock: ' + p.stock +
          (p.category ? ' · ' + escapeHtml(p.category) : '') +
          ' <span class="badge ' + (p.active ? 'on' : 'off') + '">' + (p.active ? 'visible' : 'oculto') + '</span></small></div>' +
          '<div class="actions"><button class="btn-small b-edit">✏️</button><button class="btn-small b-stock">📦</button><button class="btn-danger b-del">🗑️</button></div>';
        row.querySelector('.b-edit').addEventListener('click', function () { openModal(p); });
        row.querySelector('.b-stock').addEventListener('click', async function () {
          var v = prompt('Nuevo stock para "' + p.name + '":', p.stock);
          if (v === null) return;
          try {
            await api('/api/admin/products/' + p.id, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(Object.assign({}, p, { price: p.price_cents / 100, stock: parseInt(v, 10) || 0 })) });
            loadProducts(); showNotice('Inventario actualizado ✅');
          } catch (err) { showNotice(err.message, true); }
        });
        row.querySelector('.b-del').addEventListener('click', async function () {
          if (!confirm('¿Borrar "' + p.name + '"?')) return;
          try { await api('/api/admin/products/' + p.id, { method: 'DELETE' }); loadProducts(); showNotice('Producto borrado.'); }
          catch (err) { showNotice(err.message, true); }
        });
        list.appendChild(row);
      });
    } catch (err) { if (err.message !== 'auth') showNotice(err.message, true); }
  }

  /* Pedidos */
  var STATUS_TXT = { pending: '⏳ pendiente', paid: '✅ pagado', cancelled: '❌ cancelado', error: '⚠️ error' };
  async function loadOrders() {
    var list = document.getElementById('order-list');
    try {
      var orders = await api('/api/admin/orders');
      if (!orders.length) { list.innerHTML = '<p class="hint">Aún no hay pedidos.</p>'; return; }
      list.innerHTML = '';
      orders.forEach(function (o) {
        var items = o.items.map(function (it) {
          return it.qty + '× ' + escapeHtml(it.name) + (it.size ? ' (talla ' + escapeHtml(it.size) + ')' : '');
        }).join('<br>');
        var row = document.createElement('div');
        row.className = 'admin-row';
        var d = new Date(o.created_at * 1000);
        row.innerHTML = '<div class="info"><strong>Pedido #' + o.id + ' · ' + money(o.total_cents) + '</strong>' +
          '<small>' + items + '</small><small>' + d.toLocaleString('es-US') + ' · ' + (STATUS_TXT[o.status] || o.status) + '</small></div>';
        list.appendChild(row);
      });
    } catch (err) { if (err.message !== 'auth') showNotice(err.message, true); }
  }

  /* Ajustes */
  async function loadSettings() {
    try {
      var s = await api('/api/admin/settings');
      document.getElementById('set-store-name').value = s.store_name || '';
      document.getElementById('set-currency').value = s.currency || 'usd';
      document.getElementById('set-tagline').value = s.tagline || '';
      document.getElementById('set-horarios').value = s.info_horarios || '';
      document.getElementById('set-ubicacion').value = s.info_ubicacion || '';
      document.getElementById('set-contacto').value = s.info_contacto || '';
      document.getElementById('set-pagos').value = s.info_pagos || '';
      document.getElementById('set-envios').value = s.info_envios || '';
      setBadge('sk-status', s.stripe_secret_key);
      setBadge('pk-status', s.stripe_publishable_key);
      setBadge('wh-status', s.stripe_webhook_secret);
    } catch (err) { if (err.message !== 'auth') showNotice(err.message, true); }
  }
  function setBadge(id, info) {
    var el = document.getElementById(id);
    if (info && info.configured) { el.textContent = 'configurada ···' + info.last4; el.className = 'badge on'; }
    else { el.textContent = 'sin configurar'; el.className = 'badge off'; }
  }
  document.getElementById('settings-form').addEventListener('submit', async function (e) {
    e.preventDefault();
    var body = {
      store_name: document.getElementById('set-store-name').value,
      currency: document.getElementById('set-currency').value,
      tagline: document.getElementById('set-tagline').value,
      info_horarios: document.getElementById('set-horarios').value,
      info_ubicacion: document.getElementById('set-ubicacion').value,
      info_contacto: document.getElementById('set-contacto').value,
      info_pagos: document.getElementById('set-pagos').value,
      info_envios: document.getElementById('set-envios').value
    };
    ['stripe_secret_key', 'stripe_publishable_key', 'stripe_webhook_secret'].forEach(function (k, i) {
      var v = document.getElementById(['set-stripe-secret', 'set-stripe-pk', 'set-stripe-wh'][i]).value.trim();
      if (v) body[k] = v;
    });
    try { await api('/api/admin/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      showNotice('Ajustes guardados ✅'); loadSettings();
      document.getElementById('set-stripe-secret').value = '';
      document.getElementById('set-stripe-wh').value = '';
    } catch (err) { showNotice(err.message, true); }
  });
  document.getElementById('password-form').addEventListener('submit', async function (e) {
    e.preventDefault();
    try {
      await api('/api/admin/change-password', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current: document.getElementById('pw-current').value, new: document.getElementById('pw-new').value }) });
      showNotice('Contraseña cambiada ✅');
      document.getElementById('pw-current').value = ''; document.getElementById('pw-new').value = '';
    } catch (err) { showNotice(err.message, true); }
  });

  loadProducts();
})();
