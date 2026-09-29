/* Tienda pública: catálogo, carrito (localStorage) y checkout */
(function () {
  'use strict';
  var catalog = document.getElementById('catalog');
  var notice = document.getElementById('notice');
  var cartBtn = document.getElementById('cart-btn');
  var cartCount = document.getElementById('cart-count');
  var drawer = document.getElementById('cart-drawer');
  var backdrop = document.getElementById('drawer-backdrop');
  var cartItemsEl = document.getElementById('cart-items');
  var cartTotalEl = document.getElementById('cart-total');
  var checkoutBtn = document.getElementById('checkout-btn');
  var checkoutError = document.getElementById('checkout-error');
  var closeBtn = document.getElementById('cart-close');
  var products = [];

  function money(cents) {
    return new Intl.NumberFormat('es-HN', { style: 'currency', currency: 'HNL' }).format(cents / 100);
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

  async function loadProducts() {
    try {
      var r = await fetch('/api/products');
      products = await r.json();
    } catch (e) {
      catalog.innerHTML = '<p class="loading">No se pudieron cargar los productos. Revisa tu conexión.</p>';
      return;
    }
    if (!products.length) {
      catalog.innerHTML = '<p class="loading">Aún no hay productos en la tienda. Vuelve pronto. 👕</p>';
      return;
    }
    catalog.innerHTML = '';
    products.forEach(function (p) {
      var card = document.createElement('article');
      card.className = 'card';
      var photo = p.photo
        ? '<img src="' + p.photo + '" alt="' + escapeHtml(p.name) + '" loading="lazy">'
        : '<div class="no-photo">👕</div>';
      var sizeOpts = (p.sizes || []).map(function (s) {
        return '<option value="' + escapeHtml(s) + '">' + escapeHtml(s) + '</option>';
      }).join('');
      var sizeSel = sizeOpts ? '<select class="p-size" aria-label="Talla">' + sizeOpts + '</select>' : '';
      var stockTxt = p.stock <= 0
        ? '<span class="stock low">Agotado</span>'
        : (p.stock <= 3 ? '<span class="stock low">¡Solo quedan ' + p.stock + '!</span>'
                        : '<span class="stock">' + p.stock + ' disponibles</span>');
      card.innerHTML =
        photo +
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

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function addToCart(id, size, qty) {
    var cart = getCart();
    var line = cart.find(function (l) { return l.id === id && l.size === size; });
    if (line) line.qty += qty; else cart.push({ id: id, size: size, qty: qty });
    saveCart(cart);
    showNotice('Agregado al carrito ✅');
    setTimeout(function () { notice.classList.add('hidden'); }, 1500);
  }

  function renderCartBadge() {
    var n = getCart().reduce(function (a, l) { return a + l.qty; }, 0);
    cartCount.textContent = n;
  }

  function renderCart() {
    var cart = getCart();
    checkoutError.classList.add('hidden');
    if (!cart.length) {
      cartItemsEl.innerHTML = '<p class="loading">Tu carrito está vacío.</p>';
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
    cartTotalEl.textContent = money(total);
  }

  function bumpQty(idx, d) {
    var cart = getCart();
    cart[idx].qty += d;
    if (cart[idx].qty <= 0) cart.splice(idx, 1);
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

  checkoutBtn.addEventListener('click', async function () {
    checkoutError.classList.add('hidden');
    checkoutBtn.disabled = true;
    checkoutBtn.textContent = 'Procesando…';
    try {
      var r = await fetch('/api/checkout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items: getCart() })
      });
      var d = await r.json();
      if (r.ok && d.url) {
        window.location.href = d.url;
        return;
      }
      throw new Error(d.error || 'No se pudo iniciar el pago.');
    } catch (e) {
      checkoutError.textContent = e.message;
      checkoutError.classList.remove('hidden');
    } finally {
      checkoutBtn.disabled = false;
      checkoutBtn.textContent = 'Pagar con tarjeta 💳';
    }
  });

  loadProducts();
})();
