/* Service Worker - app de administración */
var CACHE = 'tienda-admin-v14';
var ASSETS = ['/admin', '/static/style.css', '/static/admin.js', '/admin/manifest.json'];

self.addEventListener('install', function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(ASSETS); }).then(function () { return self.skipWaiting(); }));
});
self.addEventListener('activate', function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});
self.addEventListener('fetch', function (e) {
  if (e.request.method !== 'GET') return;
  var url = new URL(e.request.url);
  if (url.origin !== self.location.origin) return; // no interceptar terceros
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/uploads/')) return; // siempre red
  if (e.request.mode === 'navigate') {
    // Navegación: red primero y solo guardar respuestas buenas (200).
    // Nunca guardar redirecciones: una 301 en caché causaba ciclo infinito en iOS.
    e.respondWith(
      fetch(e.request).then(function (res) {
        if (res.ok) {
          var copy = res.clone();
          caches.open(CACHE).then(function (c) { c.put(e.request, copy); });
        }
        return res;
      }).catch(function () { return caches.match(e.request); })
    );
    return;
  }
  e.respondWith(
    caches.match(e.request).then(function (hit) {
      return hit || fetch(e.request).then(function (res) {
        if (res.ok) {
          var copy = res.clone();
          caches.open(CACHE).then(function (c) { c.put(e.request, copy); });
        }
        return res;
      }).catch(function () { return hit; });
    })
  );
});

/* Notificaciones push: avisos de nuevos pedidos */
self.addEventListener('push', function (e) {
  var data = {};
  try { data = e.data ? e.data.json() : {}; } catch (err) {}
  var title = data.title || 'Tu Nuevo Estilo Admin';
  e.waitUntil(
    self.registration.showNotification(title, {
      body: data.body || '',
      icon: '/static/icon-admin-192.png',
      badge: '/static/icon-admin-192.png',
      tag: data.tag || 'pedido',
      renotify: true,
      data: { url: data.url || '/admin' }
    })
  );
});
self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  var url = (e.notification.data && e.notification.data.url) || '/admin';
  e.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
      for (var i = 0; i < list.length; i++) {
        if (list[i].url.indexOf('/admin') !== -1) {
          list[i].navigate(url + '#pedidos');
          return list[i].focus();
        }
      }
      return self.clients.openWindow(url + '#pedidos');
    })
  );
});
