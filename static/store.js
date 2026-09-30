/* Tienda pública: info, categorías, catálogo, carrito (localStorage) y checkout */
(function () {
  'use strict';
  var catalog = document.getElementById('catalog');
  var catalogTitle = document.getElementById('catalog-title');
  var catPills = document.getElementById('cat-pills');
  var notice = document.getElementById('notice');
  var cartBtn = document.getElementById('cart-btn');
  var cartCount = document.getElementById('cart-count');
  var drawer = document.getElementById('cart-drawer');
  var backdrop = document.getElementById('drawer-backdrop');
  var cartItemsEl = document.getElementById('cart-items');
  var cartTotalEl = document.getElementById('cart-total');
  var cartShippingEl = document.getElementById('cart-shipping');
  var checkoutBtn = document.getElementById('checkout-btn');
  var checkoutError = document.getElementById('checkout-error');
  var closeBtn = document.getElementById('cart-close');
  var infoModal = document.getElementById('info-modal');
  var products = [];
  var infoData = {};
  var activeCat = '';

  var INFO_LABELS = {
    info_horarios: 'Horarios',
    info_ubicacion: 'Ubicación',
    info_contacto: 'Contáctenos',
    info_pagos: 'Pagos',
    info_envios: 'Envíos'
  };

  function money(cents) {
    return new Intl.NumberFormat('es-HN', { style: 'currency', currency: 'HNL' }).format(cents / 100);
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function getCart() {
    try { return JSON.parse(localStorage.getItem('tienda_cart') || '[]'); } catch (e) { return []; }
  }
  function saveCart(cart) {
    localStorage.setItem('tienda_cart', JSON.stringify(cart));
    renderCartBadge();
  }
  function findProduct(id) { return products.find(function (p) { return p.id === id; }); }
  function showNotice(msg) {
    notice.textContent = msg;
    notice.classList.remove('hidden');
  }

  /* ---------- Información de la tienda (nav) ---------- */
  async function loadInfo() {
    try {
      var r = await fetch('/api/info');
      infoData = await r.json();
    } catch (e) { infoData = {}; return; }
    if (infoData.store_name) {
      document.getElementById('store-name').textContent = infoData.store_name;
      document.getElementById('hero-title').textContent = infoData.store_name;
      document.getElementById('foot-name').textContent = infoData.store_name;
      document.title = infoData.store_name;
    }
    var tag = infoData.tagline || '';
    document.getElementById('store-tagline').textContent = tag;
    document.getElementById('hero-sub').textContent = tag;
    document.getElementById('foot-tagline').textContent = tag;
    if (infoData.info_envios) document.getElementById('foot-envios').textContent = infoData.info_envios;
  }

  document.querySelectorAll('.info-nav button').forEach(function (b) {
    b.addEventListener('click', function () {
      var key = b.getAttribute('data-info');
      document.getElementById('info-title').textContent = INFO_LABELS[key] || 'Información';
      document.getElementById('info-body').textContent = infoData[key] || 'Próximamente.';
      infoModal.classList.remove('hidden');
    });
  });
  document.getElementById('info-close').addEventListener('click', function () {
    infoModal.classList.add('hidden');
  });
  infoModal.addEventListener('click', function (e) { if (e.target === infoModal) infoModal.classList.add('hidden'); });

  /* ---------- Catálogo y categorías ---------- */
  function categories() {
    var seen = {};
    products.forEach(function (p) {
      var c = (p.category || '').trim();
      if (c) seen[c] = true;
    });
    return Object.keys(seen).sort();
  }

  function renderPills() {
    var cats = categories();
    catPills.innerHTML = '';
    if (!cats.length) {
      catPills.parentElement.classList.add('hidden');
      return;
    }
    catPills.parentElement.classList.remove('hidden');
    var all = document.createElement('button');
    all.className = 'pill' + (activeCat === '' ? ' active' : '');
    all.textContent = 'Todo';
    all.addEventListener('click', function () { setCategory(''); });
    catPills.appendChild(all);
    cats.forEach(function (c) {
      var b = document.createElement('button');
      b.className = 'pill' + (activeCat === c ? ' active' : '');
      b.textContent = c;
      b.addEventListener('click', function () { setCategory(c); });
      catPills.appendChild(b);
    });
  }

  function setCategory(c) {
    activeCat = c;
    renderPills();
    renderCatalog();
    catalogTitle.textContent = c || 'Novedades';
    document.getElementById('catalogo').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function renderCatalog() {
    var list = activeCat
      ? products.filter(function (p) { return (p.category || '').trim() === activeCat; })
      : products;
    catalog.innerHTML = '';
    if (!list.length) {
      catalog.innerHTML = '<p class="loading">' +
        (products.length ? 'No hay productos en esta categoría.' : 'Aún no hay productos en la tienda. Vuelve pronto. 🛍️') +
        '</p>';
      return;
    }
    list.forEach(function (p) {
      var card = document.createElement('article');
      card.className = 'card';
      var photo = p.photo
        ? '<img src="' + p.photo + '" alt="' + escapeHtml(p.name) + '" loading="lazy">'
        : '<div class="no-photo">🛍️</div>';
      var catBadge = p.category ? '<span class="cat-badge">' + escapeHtml(p.category) + '</span>' : '';
      var sizeOpts = (p.sizes || []).map(function (s) {
        return '<option value="' + escapeHtml(s) + '">' + escapeHtml(s) + '</option>';
      }).join('');
      var sizeSel = sizeOpts ? '<select class="p-size" aria-label="Talla">' + sizeOpts + '</select>' : '';
      var stockTxt = p.stock <= 0
        ? '<span class="stock low">Agotado</span>'
        : (p.stock <= 3 ? '<span class="stock low">¡Solo quedan ' + p.stock + '!</span>'
                        : '<span class="stock">' + p.stock + ' disponibles</span>');
      card.innerHTML =
        photo + catBadge +
        '<div class="card-body">' +
          '<h3>' + escapeHtml(p.name) + '</h3>' +
          '<div class="price">' + money(p.price_cents) + '</div>' +
          stockTxt + sizeSel +
          '<div class="add-row">' +
            '<input type="number" class="p-qty" value="1" min="1" max="' + p.stock + '" aria-label="Cantidad">' +
            '<button class="btn-primary p-add"' + (p.stock <= 0 ? ' disabled' : '') + '>Agregar</button>' +
          '</div>' +
        '</div>';
      if (p.stock > 0) {
        card.querySelector('.p-add').addEventListener('click', function () {
          var size = card.querySelector('.p-size');
          var qty = parseInt(card.querySelector('.p-qty').value, 10) || 1;
          addToCart(p.id, size ? size.value : '', qty);
        });
      }
      catalog.appendChild(card);
    });
    renderCartBadge();
  }

  async function loadProducts() {
    try {
      var r = await fetch('/api/products');
      products = await r.json();
    } catch (e) {
      catalog.innerHTML = '<p class="loading">No se pudieron cargar los productos. Revisa tu conexión.</p>';
      return;
    }
    renderPills();
    renderCatalog();
  }

  /* ---------- Carrito ---------- */
  function addToCart(id, size, qty) {
    var p = findProduct(id);
    var max = p ? p.stock : 99; // no agregar más de lo disponible
    var cart = getCart();
    var line = cart.find(function (l) { return l.id === id && l.size === size; });
    var cur = line ? line.qty : 0;
    var want = cur + (qty || 1);
    if (want > max) {
      if (cur < max) {
        if (line) line.qty = max; else cart.push({ id: id, size: size, qty: max });
        saveCart(cart);
      }
      showNotice(max > 0 ? 'Solo hay ' + max + ' disponible(s) ⚠️' : 'Producto agotado');
    } else {
      if (line) line.qty = want; else cart.push({ id: id, size: size, qty: want });
      saveCart(cart);
      showNotice('Agregado al carrito ✅');
    }
    setTimeout(function () { notice.classList.add('hidden'); }, 1500);
  }

  function renderCartBadge() {
    var n = getCart().reduce(function (a, l) { return a + l.qty; }, 0);
    cartCount.textContent = n;
  }

  /* Envío: L150 hasta 5 artículos, L200 si son más; gratis al recoger en oficina */
  function cartQty(cart) {
    return cart.reduce(function (a, l) { return a + (l.qty || 0); }, 0);
  }
  function deliveryMethod() {
    var r = document.querySelector('input[name="delivery"]:checked');
    return r ? r.value : 'domicilio';
  }
  function shippingCents(cart) {
    if (deliveryMethod() === 'oficina') return 0;
    return cartQty(cart) <= 5 ? 15000 : 20000;
  }

  function renderCart() {
    var cart = getCart();
    checkoutError.classList.add('hidden');
    if (!cart.length) {
      cartItemsEl.innerHTML = '<p class="loading">Tu carrito está vacío.</p>';
      if (cartShippingEl) cartShippingEl.textContent = money(0);
      cartTotalEl.textContent = money(0);
      checkoutBtn.disabled = true;
      return;
    }
    checkoutBtn.disabled = false;
    cartItemsEl.innerHTML = '';
    var total = 0;
    cart.forEach(function (line, idx) {
      var p = findProduct(line.id);
      if (!p) return;
      total += p.price_cents * line.qty;
      var div = document.createElement('div');
      div.className = 'cart-item';
      div.innerHTML =
        (p.photo ? '<img src="' + p.photo + '" alt="">' : '<img alt="">') +
        '<div class="info"><strong>' + escapeHtml(p.name) + '</strong>' +
        '<small>' + (line.size ? 'Talla ' + escapeHtml(line.size) + ' · ' : '') + money(p.price_cents) + ' c/u</small></div>' +
        '<div class="qty-ctl">' +
          '<button class="btn-small" data-a="dec" aria-label="Quitar uno">−</button>' +
          '<span>' + line.qty + '</span>' +
          '<button class="btn-small" data-a="inc" aria-label="Agregar uno">＋</button>' +
          '<button class="btn-small" data-a="rm" aria-label="Eliminar">🗑️</button>' +
        '</div>';
      div.querySelector('[data-a="dec"]').addEventListener('click', function () { bumpQty(idx, -1); });
      div.querySelector('[data-a="inc"]').addEventListener('click', function () { bumpQty(idx, 1); });
      div.querySelector('[data-a="rm"]').addEventListener('click', function () { bumpQty(idx, -999); });
      cartItemsEl.appendChild(div);
    });
    var shipping = shippingCents(cart);
    if (cartShippingEl) cartShippingEl.textContent = shipping ? money(shipping) : 'Gratis';
    cartTotalEl.textContent = money(total + shipping);
  }

  function bumpQty(idx, d) {
    var cart = getCart();
    var line = cart[idx];
    if (!line) return;
    if (d > 0) {
      var p = findProduct(line.id);
      var max = p ? p.stock : 99; // no subir más de lo disponible
      if (line.qty + d > max) {
        showNotice('Solo hay ' + max + ' disponible(s) ⚠️');
        setTimeout(function () { notice.classList.add('hidden'); }, 1500);
        return;
      }
    }
    line.qty += d;
    if (line.qty <= 0) cart.splice(idx, 1);
    saveCart(cart);
    renderCart();
  }

  function openDrawer() {
    renderCart();
    drawer.classList.remove('hidden');
    backdrop.classList.remove('hidden');
  }
  function closeDrawer() {
    drawer.classList.add('hidden');
    backdrop.classList.add('hidden');
  }

  cartBtn.addEventListener('click', openDrawer);
  closeBtn.addEventListener('click', closeDrawer);
  backdrop.addEventListener('click', closeDrawer);
  Array.prototype.forEach.call(
    document.querySelectorAll('input[name="delivery"]'),
    function (r) { r.addEventListener('change', renderCart); }
  );

  /* Datos de entrega: se guardan en el teléfono para no pedirlos cada vez */
  var CUST_KEY = 'usstyle_customer';
  function readCustomer() {
    return {
      name: document.getElementById('cust-name').value.trim(),
      address: document.getElementById('cust-address').value.trim(),
      city: document.getElementById('cust-city').value.trim(),
      department: document.getElementById('cust-department').value,
      delivery: (document.querySelector('input[name="delivery"]:checked') || {}).value || '',
      payment: (document.querySelector('input[name="payment"]:checked') || {}).value || ''
    };
  }
  function prefillCustomer() {
    try {
      var c = JSON.parse(localStorage.getItem(CUST_KEY) || 'null');
      if (!c) return;
      if (c.name) document.getElementById('cust-name').value = c.name;
      if (c.address) document.getElementById('cust-address').value = c.address;
      if (c.city) document.getElementById('cust-city').value = c.city;
      if (c.department) document.getElementById('cust-department').value = c.department;
      if (c.delivery) {
        var r = document.querySelector('input[name="delivery"][value="' + c.delivery + '"]');
        if (r) r.checked = true;
      }
      if (c.payment) {
        var p = document.querySelector('input[name="payment"][value="' + c.payment + '"]');
        if (p) p.checked = true;
      }
    } catch (e) {}
  }

  var WHATSAPP = '+504 9527-3914';
  function showOrderSuccess(d) {
    var cart = getCart();
    saveCart([]);
    renderCart();
    document.querySelector('.delivery-form').classList.add('hidden');
    document.querySelectorAll('.drawer-foot .total-row').forEach(function (el) { el.classList.add('hidden'); });
    checkoutBtn.classList.add('hidden');
    checkoutError.classList.add('hidden');
    document.getElementById('order-success-num').textContent =
      'Tu número de pedido es #' + d.order_id + ' · Total: ' + money(d.total_cents) +
      (d.shipping_cents ? ' (incluye ' + money(d.shipping_cents) + ' de envío)' : ' (sin costo de envío)');
    var detail = d.delivery === 'oficina'
      ? 'Te avisaremos por WhatsApp cuando tu pedido esté listo para recoger en la oficina. 🏢'
      : 'Haremos tu envío a domicilio en 2 a 4 días hábiles. 📦';
    document.getElementById('order-success-detail').textContent = detail;
    var pay = d.payment === 'deposito'
      ? '💳 Haz tu depósito o transferencia en Banco Atlántida por ' + money(d.total_cents) +
        ' y envíanos tu comprobante por WhatsApp al ' + WHATSAPP +
        ' para procesar tu envío cuanto antes.'
      : '💵 Pagarás ' + money(d.total_cents) + ' en efectivo al recibir tu pedido. ¡Gracias por tu compra! 🙌';
    document.getElementById('order-success-pay').textContent = pay;
    document.getElementById('order-success').classList.remove('hidden');
  }
  document.getElementById('order-success-close').addEventListener('click', function () {
    document.getElementById('order-success').classList.add('hidden');
    document.querySelector('.delivery-form').classList.remove('hidden');
    document.querySelectorAll('.drawer-foot .total-row').forEach(function (el) { el.classList.remove('hidden'); });
    checkoutBtn.classList.remove('hidden');
    closeDrawer();
  });

  checkoutBtn.addEventListener('click', async function () {
    checkoutError.classList.add('hidden');
    var customer = readCustomer();
    if (!customer.name || !customer.address || !customer.city || !customer.department) {
      checkoutError.textContent = 'Completa tu nombre, dirección, ciudad y departamento para el envío.';
      checkoutError.classList.remove('hidden');
      return;
    }
    try { localStorage.setItem(CUST_KEY, JSON.stringify(customer)); } catch (e) {}
    checkoutBtn.disabled = true;
    checkoutBtn.textContent = 'Procesando…';
    try {
      var r = await fetch('/api/checkout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items: getCart(), customer: customer })
      });
      var d = await r.json();
      if (!r.ok || !d.order_id) throw new Error(d.error || 'No se pudo crear el pedido.');
      showOrderSuccess(d);
    } catch (e) {
      checkoutError.textContent = e.message;
      checkoutError.classList.remove('hidden');
    } finally {
      checkoutBtn.disabled = false;
      checkoutBtn.textContent = 'Confirmar pedido ✅';
    }
  });

  loadInfo();
  loadProducts();
  prefillCustomer();
})();
