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
      if (t.dataset.tab === 'ganancias') loadProfit();
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
    document.getElementById('p-cost').value = (p && p.cost_cents) ? (p.cost_cents / 100).toFixed(2) : '';
    document.getElementById('p-stock').value = p ? p.stock : 0;
    document.getElementById('p-sizes').value = p ? (p.sizes || []).join(', ') : '';
    document.getElementById('p-category').value = p ? (p.category || '') : '';
    document.getElementById('p-sku').value = p ? p.sku : '';
    editingPid = p ? p.id : null;
    galleryItems = p ? (p.photo_items || []) : [];
    coverUrl = p ? (p.photo || '') : '';
    pendingUploads = [];
    renderPhotoGrid();
    document.getElementById('p-active').checked = p ? p.active : true;
    document.getElementById('product-error').classList.add('hidden');
    modal.classList.remove('hidden');
  }
  function closeModal() { modal.classList.add('hidden'); }
  document.getElementById('modal-close').addEventListener('click', closeModal);
  modal.addEventListener('click', function (e) { if (e.target === modal) closeModal(); });
  document.getElementById('new-product-btn').addEventListener('click', function () { openModal(null); });

  /* Fotos múltiples del producto.
     En un producto nuevo las fotos quedan pendientes hasta guardar;
     en uno existente se agregan, borran o cambian de portada al momento. */
  var editingPid = null;
  var galleryItems = [];   // [{id, url}] fotos guardadas (sin la portada)
  var coverUrl = '';       // url de la portada actual
  var pendingUploads = []; // [{upload_id, url}] fotos por guardar (producto nuevo)
  var photosGrid = document.getElementById('p-photos-grid');

  function photoThumb(src, badge, buttons) {
    var d = document.createElement('div');
    d.className = 'photo-thumb';
    d.innerHTML = '<img src="' + src + '" alt="">' +
      (badge ? '<span class="photo-badge">' + badge + '</span>' : '') +
      '<div class="photo-btns">' + buttons + '</div>';
    return d;
  }

  function renderPhotoGrid() {
    photosGrid.innerHTML = '';
    if (editingPid === null) {
      pendingUploads.forEach(function (u, i) {
        photosGrid.appendChild(photoThumb(u.url,
          i === 0 ? '⭐ Portada' : '',
          '<button type="button" data-pdel="' + i + '" title="Quitar">✕</button>'));
      });
    } else {
      if (coverUrl) {
        photosGrid.appendChild(photoThumb(coverUrl, '⭐ Portada',
          '<button type="button" data-coverdel title="Quitar portada">✕</button>'));
      }
      galleryItems.forEach(function (it) {
        photosGrid.appendChild(photoThumb(it.url, '',
          '<button type="button" data-makecover="' + it.id + '" title="Hacer portada">⭐</button>' +
          '<button type="button" data-gdel="' + it.id + '" title="Borrar">✕</button>'));
      });
    }
    document.getElementById('p-photo').value = coverUrl;
  }

  function syncPhotoState(st) {
    coverUrl = st.photo || '';
    galleryItems = st.photo_items || [];
    renderPhotoGrid();
  }

  photosGrid.addEventListener('click', async function (e) {
    var b = e.target.closest ? e.target.closest('button') : null;
    if (!b) return;
    try {
      if (b.hasAttribute('data-pdel')) {
        pendingUploads.splice(parseInt(b.getAttribute('data-pdel'), 10), 1);
        renderPhotoGrid();
      } else if (b.hasAttribute('data-coverdel')) {
        coverUrl = '';
        renderPhotoGrid();
        showNotice('Portada quitada. Guarda el producto para aplicar.');
      } else if (b.hasAttribute('data-gdel')) {
        var st = await api('/api/admin/products/' + editingPid + '/photos/' + b.getAttribute('data-gdel'), { method: 'DELETE' });
        syncPhotoState(st);
        loadProducts();
      } else if (b.hasAttribute('data-makecover')) {
        var st2 = await api('/api/admin/products/' + editingPid + '/photos/' + b.getAttribute('data-makecover') + '/cover', { method: 'POST' });
        syncPhotoState(st2);
        loadProducts();
      }
    } catch (err) { showNotice(err.message, true); }
  });

  document.getElementById('p-photos-add').addEventListener('click', function () {
    document.getElementById('p-photos-file').click();
  });

  document.getElementById('p-photos-file').addEventListener('change', async function (e) {
    var files = Array.prototype.slice.call(e.target.files || []);
    e.target.value = '';
    if (!files.length) return;
    for (var i = 0; i < files.length; i++) {
      var fd = new FormData();
      fd.append('file', files[i]);
      try {
        var d = await api('/api/upload', { method: 'POST', body: fd });
        if (editingPid === null) {
          pendingUploads.push({ upload_id: d.upload_id, url: d.url });
          renderPhotoGrid();
        } else {
          var st = await api('/api/admin/products/' + editingPid + '/photos', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ upload_id: d.upload_id })
          });
          syncPhotoState(st);
        }
      } catch (err) { showNotice(err.message, true); }
    }
    if (editingPid !== null) loadProducts();
    showNotice('Foto(s) agregada(s) ✅');
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
      cost: document.getElementById('p-cost').value,
      stock: document.getElementById('p-stock').value,
      sizes: document.getElementById('p-sizes').value,
      category: document.getElementById('p-category').value,
      sku: document.getElementById('p-sku').value,
      photo: document.getElementById('p-photo').value,
      active: document.getElementById('p-active').checked
    };
    if (!id) body.photo_upload_ids = pendingUploads.map(function (u) { return u.upload_id; });
    try {
      var saved;
      if (id) saved = await api('/api/admin/products/' + id, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      else saved = await api('/api/admin/products', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      closeModal(); loadProducts(); showNotice('Producto guardado ✅');
      if (!id && saved && saved.id) openQrModal(saved); // QR creado automáticamente
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
        var costC = p.cost_cents || 0;
        var profitLine;
        if (costC > 0) {
          var unit = p.price_cents - costC;
          var pct = p.price_cents > 0 ? Math.round(unit / p.price_cents * 100) : 0;
          profitLine = '<small>💰 Costo: ' + money(costC) + ' · Ganancia/u: ' + money(unit) + ' (' + pct + '%)</small>';
        } else {
          profitLine = '<small class="no-cost">💰 Sin costo registrado — tócalo ✏️ para agregarlo</small>';
        }
        row.innerHTML =
          (p.photo ? '<img class="thumb" src="' + p.photo + '" alt="">' : '<img class="thumb" alt="">') +
          '<div class="info"><strong>' + escapeHtml(p.name) + '</strong>' +
          '<small>' + money(p.price_cents) + ' · Stock: ' + p.stock +
          (p.category ? ' · ' + escapeHtml(p.category) : '') +
          ' <span class="badge ' + (p.active ? 'on' : 'off') + '">' + (p.active ? 'visible' : 'oculto') + '</span></small>' +
          profitLine + '</div>' +
          '<div class="actions"><button class="btn-small b-qr" title="Código QR para clientes">QR</button><button class="btn-small b-edit">✏️</button><button class="btn-small b-stock">📦</button><button class="btn-danger b-del">🗑️</button></div>';
        row.querySelector('.b-qr').addEventListener('click', function () { openQrModal(p); });
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

  /* QR del producto para el cliente: al escanearlo abre la página del
     producto en la tienda (fotos, precio, detalles). */
  var storeName = 'Tu Nuevo Estilo';
  api('/api/admin/me').then(function (me) { if (me && me.store_name) storeName = me.store_name; }).catch(function () {});

  /* Tarjeta QR estilo canasta: se genera sola con los datos en vivo del
     producto (precio, tallas, descripción y sitio web; sin el stock).
     Como los datos se leen al momento, siempre sale actualizada. */
  function openQrModal(p) {
    var old = document.getElementById('qr-modal');
    if (old) old.remove();
    var sizes = (p.sizes || []).join(' · ');
    var ov = document.createElement('div');
    ov.id = 'qr-modal';
    ov.className = 'modal';
    ov.innerHTML =
      '<div class="modal-card basket-card">' +
        '<div class="drawer-head no-print"><h2>Etiqueta QR</h2>' +
        '<button class="icon-btn" aria-label="Cerrar">✕</button></div>' +
        '<div class="basket-head"><span class="basket-ico">🛒</span><span>' + escapeHtml(storeName) + '</span></div>' +
        '<div class="basket-top">' +
          (p.photo ? '<img class="basket-photo" src="' + p.photo + '" alt="">' : '') +
          '<div class="basket-info"><h3>' + escapeHtml(p.name) + '</h3>' +
          '<div class="price">' + money(p.price_cents) + '</div>' +
          (sizes ? '<div class="basket-sizes">Tallas: ' + escapeHtml(sizes) + '</div>' : '') +
          '</div>' +
        '</div>' +
        (p.description ? '<p class="basket-desc">' + escapeHtml(p.description) + '</p>' : '') +
        '<div class="basket-qr">' +
          '<img class="qr-img" src="/api/admin/products/' + p.id + '/qr" alt="QR de ' + escapeHtml(p.name) + '">' +
          '<p>Escanea para ver más fotos y comprar en línea</p>' +
        '</div>' +
        '<div class="basket-web">' + escapeHtml(location.host) + '</div>' +
        '<div class="basket-actions no-print">' +
          '<button class="btn-primary" id="qr-print-btn">🖨️ Imprimir etiqueta</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(ov);
    ov.querySelector('.icon-btn').addEventListener('click', function () { ov.remove(); });
    ov.addEventListener('click', function (e) { if (e.target === ov) ov.remove(); });
    ov.querySelector('#qr-print-btn').addEventListener('click', function () {
      document.body.classList.add('qr-printing');
      window.print();
      setTimeout(function () { document.body.classList.remove('qr-printing'); }, 800);
    });
  }

  /* Ganancias: tarjetas resumen + gráficas circulares (SVG puro, sin dependencias).
     Los datos vienen de /api/admin/stats/profit y se recalculan con el
     inventario real cada vez que se abre la pestaña. */
  var PIE_COLORS = ['#0a2a5e', '#c1121f', '#f0b429', '#1e4fa3', '#e05252',
                    '#f5c95c', '#06204a', '#8f0d16', '#b98a1f'];

  function profitCard(ico, label, value) {
    return '<div class="profit-card"><span class="pc-ico">' + ico + '</span>' +
      '<span class="pc-label">' + label + '</span>' +
      '<span class="pc-value">' + value + '</span></div>';
  }

  function legendHtml(rows) {
    return rows.map(function (r) {
      return '<div class="leg-row"><span class="sw" style="background:' + r.color + '"></span>' +
        '<span class="leg-label">' + escapeHtml(r.label) + '</span>' +
        '<span class="leg-val">' + r.amount +
        (r.pct !== undefined ? ' <em>(' + r.pct + '%)</em>' : '') + '</span></div>';
    }).join('');
  }

  function drawDonut(s) {
    var box = document.getElementById('chart-donut');
    var leg = document.getElementById('legend-donut');
    var total = s.investment_cents + s.potential_profit_cents;
    if (total <= 0) {
      box.innerHTML = '<p class="hint">Aún no hay inventario con valor.</p>';
      leg.innerHTML = '';
      return;
    }
    var r = 62, circ = 2 * Math.PI * r;
    var invLen = s.investment_cents / total * circ;
    box.innerHTML =
      '<svg viewBox="0 0 170 170" class="donut" role="img" aria-label="Inversión versus ganancia potencial">' +
      '<circle cx="85" cy="85" r="' + r + '" fill="none" stroke="#e5e7eb" stroke-width="28"/>' +
      '<circle cx="85" cy="85" r="' + r + '" fill="none" stroke="#0a2a5e" stroke-width="28"' +
      ' stroke-dasharray="' + invLen.toFixed(2) + ' ' + circ.toFixed(2) + '" transform="rotate(-90 85 85)"/>' +
      '<circle cx="85" cy="85" r="' + r + '" fill="none" stroke="#f0b429" stroke-width="28"' +
      ' stroke-dasharray="' + (circ - invLen).toFixed(2) + ' ' + circ.toFixed(2) + '"' +
      ' stroke-dashoffset="' + (-invLen).toFixed(2) + '" transform="rotate(-90 85 85)"/>' +
      '<text x="85" y="82" text-anchor="middle" class="donut-num">' + money(total) + '</text>' +
      '<text x="85" y="100" text-anchor="middle" class="donut-lbl">valor del inventario</text>' +
      '</svg>';
    leg.innerHTML = legendHtml([
      { color: '#0a2a5e', label: 'Inversión (costo × stock)', amount: money(s.investment_cents), pct: Math.round(s.investment_cents / total * 100) },
      { color: '#f0b429', label: 'Ganancia potencial', amount: money(s.potential_profit_cents), pct: Math.round(s.potential_profit_cents / total * 100) }
    ]);
  }

  function piePath(cx, cy, r, a0, a1) {
    var x0 = (cx + r * Math.cos(a0)).toFixed(2), y0 = (cy + r * Math.sin(a0)).toFixed(2);
    var x1 = (cx + r * Math.cos(a1)).toFixed(2), y1 = (cy + r * Math.sin(a1)).toFixed(2);
    var large = (a1 - a0) > Math.PI ? 1 : 0;
    return 'M' + cx + ',' + cy + ' L' + x0 + ',' + y0 +
      ' A' + r + ',' + r + ' 0 ' + large + ' 1 ' + x1 + ',' + y1 + ' Z';
  }

  function drawPie(s) {
    var box = document.getElementById('chart-pie');
    var leg = document.getElementById('legend-pie');
    var items = (s.by_product || []).filter(function (p) { return p.total_profit_cents > 0; });
    var total = items.reduce(function (a, p) { return a + p.total_profit_cents; }, 0);
    if (!items.length || total <= 0) {
      box.innerHTML = '<p class="hint">Registra el costo de tus productos para ver la ganancia por producto.</p>';
      leg.innerHTML = '';
      return;
    }
    var top = items.slice(0, 8);
    var rest = items.slice(8).reduce(function (a, p) { return a + p.total_profit_cents; }, 0);
    var slices = top.map(function (p) { return { label: p.name, value: p.total_profit_cents }; });
    if (rest > 0) slices.push({ label: 'Otros', value: rest });
    var a = -Math.PI / 2, cx = 85, cy = 85, r = 72;
    var paths = slices.map(function (sl, i) {
      var a1 = a + sl.value / total * 2 * Math.PI;
      var d = piePath(cx, cy, r, a, a1);
      a = a1;
      return '<path d="' + d + '" fill="' + PIE_COLORS[i % PIE_COLORS.length] + '"/>';
    }).join('');
    box.innerHTML = '<svg viewBox="0 0 170 170" class="pie" role="img" aria-label="Ganancia potencial por producto">' + paths + '</svg>';
    leg.innerHTML = legendHtml(slices.map(function (sl, i) {
      return { color: PIE_COLORS[i % PIE_COLORS.length], label: sl.label, amount: money(sl.value), pct: Math.round(sl.value / total * 100) };
    }));
  }

  async function loadProfit() {
    var box = document.getElementById('profit-cards');
    var warn = document.getElementById('profit-warn');
    try {
      var s = await api('/api/admin/stats/profit');
      box.innerHTML =
        profitCard('💰', 'Inversión en inventario', money(s.investment_cents)) +
        profitCard('🏷️', 'Valor a precio de venta', money(s.sale_value_cents)) +
        profitCard('📈', 'Ganancia potencial', money(s.potential_profit_cents)) +
        profitCard('📊', 'Margen promedio', s.avg_margin_pct + '%');
      if (s.products_without_cost > 0) {
        warn.textContent = '⚠️ ' + s.products_without_cost +
          ' producto(s) con stock aún no tienen costo registrado: la ganancia mostrada es parcial. ' +
          'Tócalos ✏️ en la pestaña Productos para agregar el costo.';
        warn.classList.remove('hidden');
      } else {
        warn.classList.add('hidden');
      }
      drawDonut(s);
      drawPie(s);
    } catch (err) { if (err.message !== 'auth') showNotice(err.message, true); }
  }

  /* Pedidos */
  var STATUS_TXT = { pending: '⏳ pendiente', paid: '✅ pagado', cancelled: '❌ cancelado', error: '⚠️ error' };
  var FST_TXT = {
    pending: '🕐 Pendiente de pago', packing: '📦 En empaquetamiento',
    ready: '🎁 Listo para envío', shipped: '🚚 Enviado',
    delivered: '✅ Entregado', cancelled: '❌ Cancelado'
  };
  function waNumber(raw) {
    var d = String(raw || '').replace(/\D/g, '');
    if (d.length === 11 && d.indexOf('504') === 0) d = d.slice(3);
    if (d.length === 8) d = '504' + d;
    return d;
  }
  function waLink(o, kind) {
    var num = waNumber(o.customer_phone);
    if (num.length < 11) return '';
    var name = (o.customer_name || 'cliente').split(' ')[0];
    var msg;
    if (kind === 'confirmado') {
      msg = '¡Hola ' + name + '! Tu pedido #' + o.id + ' de *Tu Nuevo Estilo* fue confirmado ✅' +
        ' y ya está en proceso de empaquetamiento 📦.' +
        ' Te avisaremos por aquí cuando tu paquete esté listo.';
    } else {
      msg = '¡Hola ' + name + '! Tu paquete del pedido #' + o.id + ' de *Tu Nuevo Estilo*' +
        ' ya está envuelto y listo 🎁. La compañía de envíos lo recogerá pronto.' +
        (o.tracking_number ? ' Número de guía: ' + o.tracking_number + '.' : '');
    }
    return 'https://wa.me/' + num + '?text=' + encodeURIComponent(msg);
  }
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
        var deliveryTxt = '';
        if (o.customer_name) {
          var place = [o.customer_address, o.customer_city, o.customer_department].filter(function (x) { return x; }).join(', ');
          var method = o.delivery_method === 'oficina' ? '🏢 Recoger en oficina cercana' : '🏠 Envío a domicilio';
          deliveryTxt = '<small>👤 ' + escapeHtml(o.customer_name) + (place ? ' · ' + escapeHtml(place) : '') + '</small>' +
            '<small>' + method + '</small>';
          if (o.delivery_method === 'domicilio' && o.authorized_receiver) {
            deliveryTxt += '<small>🙋 Persona autorizada a recibir: ' + escapeHtml(o.authorized_receiver) + '</small>';
          }
        }
        var contactTxt = '';
        if (o.customer_phone) contactTxt += '<small>📱 ' + escapeHtml(o.customer_phone) + '</small>';
        if (o.whatsapp_optin) contactTxt += '<small>💬 Aceptó avisos por WhatsApp</small>';
        var payTxt = o.payment_method === 'deposito' ? '🏦 Depósito B. Atlántida'
          : o.payment_method === 'efectivo' ? '💵 Efectivo' : '';
        var fst = o.fulfillment_status || 'pending';
        var fstOpts = Object.keys(FST_TXT).map(function (k) {
          return '<option value="' + k + '"' + (k === fst ? ' selected' : '') + '>' + FST_TXT[k] + '</option>';
        }).join('');
        var actions = '';
        if (o.status === 'pending') {
          actions = '<div class="actions"><button class="btn-small mark-paid">✅ Marcar pagado</button>' +
            '<button class="btn-small del-order">🗑️ Eliminar</button></div>';
        } else if (o.status === 'paid' && !o.auto_paid) {
          actions = '<div class="actions"><button class="btn-small mark-unpaid">↩️ No pagado</button>' +
            '<button class="btn-small del-order">🗑️ Eliminar</button></div>';
        } else if (o.status !== 'paid') {
          actions = '<div class="actions"><button class="btn-small del-order">🗑️ Eliminar</button></div>';
        }
        // Si fue pago automático confirmado: sin botones, el pedido está protegido.
        var statusTxt = (o.status === 'paid' && o.auto_paid) ? '✅ pagado (automático)' : (STATUS_TXT[o.status] || o.status);
        var waBtns = '';
        var waConf = waLink(o, 'confirmado'), waReady = waLink(o, 'listo');
        if (waConf) waBtns += '<button class="btn-small wa-conf" title="Abrir WhatsApp con el aviso de confirmación">💬 Avisar confirmación</button>';
        if (waReady) waBtns += '<button class="btn-small wa-ready" title="Abrir WhatsApp con el aviso de paquete listo">💬 Avisar listo</button>';
        if (waBtns) waBtns = '<div class="actions">' + waBtns + '</div>';
        row.innerHTML = '<div class="info"><strong>Pedido #' + o.id + ' · ' + money(o.total_cents) + '</strong>' +
          '<small>' + items + '</small>' + deliveryTxt + contactTxt +
          (payTxt ? '<small>' + payTxt + '</small>' : '') +
          (o.shipping_cents ? '<small>🚚 Envío: ' + money(o.shipping_cents) + '</small>' : '') +
          '<small>' + d.toLocaleString('es-US') + ' · ' + statusTxt + '</small>' +
          '<div class="fst-row"><span class="fst-badge">' + (FST_TXT[fst] || fst) + '</span>' +
          '<select class="fst-select" aria-label="Cambiar estado del pedido">' + fstOpts + '</select></div>' +
          '<div class="track-row"><input class="track-input" type="text" placeholder="N.º de guía" value="' +
          escapeHtml(o.tracking_number || '') + '">' +
          '<button class="btn-small save-track">💾 Guardar guía</button></div></div>' +
          actions + waBtns;
        (function (id, rowEl, order) {
          var paidBtn = rowEl.querySelector('.mark-paid');
          if (paidBtn) paidBtn.addEventListener('click', async function () {
            if (!confirm('¿Confirmas que recibiste el pago del pedido #' + id + '? Se descontará el inventario.')) return;
            paidBtn.disabled = true;
            try {
              await api('/api/admin/orders/' + id + '/paid', { method: 'POST' });
              loadOrders(); showNotice('Pago confirmado ✅ El pedido pasó a empaquetamiento.');
            } catch (err) { if (err.message !== 'auth') showNotice(err.message, true); paidBtn.disabled = false; }
          });
          var unpaidBtn = rowEl.querySelector('.mark-unpaid');
          if (unpaidBtn) unpaidBtn.addEventListener('click', async function () {
            if (!confirm('¿Marcar el pedido #' + id + ' como NO pagado? Las unidades volverán al inventario.')) return;
            unpaidBtn.disabled = true;
            try {
              await api('/api/admin/orders/' + id + '/unpaid', { method: 'POST' });
              loadOrders();
            } catch (err) { if (err.message !== 'auth') showNotice(err.message, true); unpaidBtn.disabled = false; }
          });
          var delBtn = rowEl.querySelector('.del-order');
          if (delBtn) delBtn.addEventListener('click', async function () {
            if (!confirm('¿Eliminar el pedido #' + id + ' para siempre? Si estaba pagado, las unidades volverán al inventario.')) return;
            delBtn.disabled = true;
            try {
              await api('/api/admin/orders/' + id, { method: 'DELETE' });
              loadOrders();
            } catch (err) { if (err.message !== 'auth') showNotice(err.message, true); delBtn.disabled = false; }
          });
          var fstSel = rowEl.querySelector('.fst-select');
          fstSel.addEventListener('change', async function () {
            var trk = rowEl.querySelector('.track-input').value.trim();
            fstSel.disabled = true;
            try {
              await api('/api/admin/orders/' + id + '/status', { method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ status: fstSel.value, tracking_number: trk }) });
              loadOrders(); showNotice('Estado actualizado ✅');
            } catch (err) { if (err.message !== 'auth') showNotice(err.message, true); fstSel.disabled = false; }
          });
          var saveTrk = rowEl.querySelector('.save-track');
          saveTrk.addEventListener('click', async function () {
            var trk = rowEl.querySelector('.track-input').value.trim();
            saveTrk.disabled = true;
            try {
              await api('/api/admin/orders/' + id + '/status', { method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ status: fstSel.value, tracking_number: trk }) });
              showNotice('Guía guardada ✅');
            } catch (err) { if (err.message !== 'auth') showNotice(err.message, true); }
            saveTrk.disabled = false;
          });
          var waC = rowEl.querySelector('.wa-conf');
          if (waC) waC.addEventListener('click', function () { window.open(waLink(order, 'confirmado'), '_blank'); });
          var waR = rowEl.querySelector('.wa-ready');
          if (waR) waR.addEventListener('click', function () { window.open(waLink(order, 'listo'), '_blank'); });
        })(o.id, row, o);
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
      document.getElementById('set-wa-channel').value = s.whatsapp_channel_url || '';
      setBadgeText('wa-status', s.whatsapp_channel_url);
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
  function setBadgeText(id, url) {
    var el = document.getElementById(id);
    if (url && url.trim()) { el.textContent = 'visible en la tienda'; el.className = 'badge on'; }
    else { el.textContent = 'oculto (sin enlace)'; el.className = 'badge off'; }
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
      info_envios: document.getElementById('set-envios').value,
      whatsapp_channel_url: document.getElementById('set-wa-channel').value
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
