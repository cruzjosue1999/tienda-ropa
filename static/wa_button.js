/* Botón flotante del canal de WhatsApp: lógica pura (testeable sin DOM).
   Devuelve la URL válida para el href, o null si el botón debe quedar oculto. */
(function () {
  'use strict';
  function waChannelHref(url) {
    url = (url || '').trim();
    if (!url) return null;
    if (!/^https?:\/\//i.test(url)) return null;
    return url;
  }
  var api = { href: waChannelHref };
  if (typeof window !== 'undefined') window.WaButton = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})();
