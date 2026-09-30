/* Búsqueda y filtros del catálogo público — lógica pura, sin DOM.
   Se carga antes de store.js y expone window.ProductFilters.
   Se puede probar con node: node tests/js/test_product_filters.js */
(function (global) {
  'use strict';

  // Minúsculas + sin tildes, para búsqueda insensible a mayúsculas/tildes.
  function normalizeText(s) {
    return String(s || '')
      .toLowerCase()
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '');
  }

  // Rangos de precio en centavos de lempira.
  var PRICE_RANGES = {
    all: null,
    lt200: { min: 0, max: 19999 },       // Menos de L200
    'mid200-500': { min: 20000, max: 50000 }, // L200 – L500
    gt500: { min: 50001, max: Infinity } // Más de L500
  };

  // Todas las palabras de la búsqueda deben aparecer (AND) en
  // nombre + descripción + categoría.
  function matchesQuery(p, q) {
    if (!q) return true;
    var hay = normalizeText([p.name, p.description, p.category].join(' '));
    var words = q.split(/\s+/);
    for (var i = 0; i < words.length; i++) {
      if (words[i] && hay.indexOf(words[i]) === -1) return false;
    }
    return true;
  }

  function matchesPrice(p, range) {
    var r = PRICE_RANGES[range];
    if (!r) return true;
    var price = p.price_cents || 0;
    return price >= r.min && price <= r.max;
  }

  // opts: { query, category, avail: 'all'|'in', priceRange, sort: 'rel'|'asc'|'desc'|'new' }
  // 'rel' conserva el orden original (el servidor ya manda novedades primero).
  function applyProductFilters(products, opts) {
    opts = opts || {};
    var q = normalizeText((opts.query || '').trim());
    var list = (products || []).filter(function (p) {
      if (opts.category && (p.category || '').trim() !== opts.category) return false;
      if (opts.avail === 'in' && !(p.stock > 0)) return false;
      if (!matchesPrice(p, opts.priceRange || 'all')) return false;
      if (!matchesQuery(p, q)) return false;
      return true;
    });
    var sort = opts.sort || 'rel';
    if (sort === 'asc') {
      list.sort(function (a, b) { return (a.price_cents || 0) - (b.price_cents || 0); });
    } else if (sort === 'desc') {
      list.sort(function (a, b) { return (b.price_cents || 0) - (a.price_cents || 0); });
    } else if (sort === 'new') {
      list.sort(function (a, b) {
        var d = String(b.created_at || '').localeCompare(String(a.created_at || ''));
        return d !== 0 ? d : (b.id || 0) - (a.id || 0);
      });
    }
    return list;
  }

  global.ProductFilters = {
    normalizeText: normalizeText,
    applyProductFilters: applyProductFilters,
    PRICE_RANGES: PRICE_RANGES
  };
})(typeof window !== 'undefined' ? window : globalThis);
