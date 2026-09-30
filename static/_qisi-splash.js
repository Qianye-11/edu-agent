/* Apple-style entrance only. It intentionally has no settings, routing, or
   application-state behavior. */
(function () {
  'use strict';

  try {
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  } catch (error) {}
  if (document.querySelector('.qisi-splash')) return;

  var chars = ['欢', '迎', '使', '用', '启', '思'];
  var title = chars.map(function (char) {
    return '<span class="qs-w"><i>' + char + '</i></span>';
  }).join('');
  var splash = document.createElement('div');
  splash.className = 'qisi-splash';
  splash.setAttribute('aria-hidden', 'true');
  splash.innerHTML =
    '<span class="qs-si">' +
      '<span class="qs-glow" aria-hidden="true">' +
        '<i class="qs-gb qs-gb1"></i><i class="qs-gb qs-gb2"></i>' +
        '<i class="qs-gb qs-gb3"></i><i class="qs-gb qs-gb4"></i>' +
      '</span>' +
      '<span class="qs-vrule" aria-hidden="true"></span>' +
      '<span class="qs-streak" aria-hidden="true"></span>' +
      '<div class="qs-title">' + title + '</div>' +
      '<div class="qs-hrule" aria-hidden="true"></div>' +
      '<div class="qs-subtitle">启学，让思考发生</div>' +
    '</span>';
  document.body.appendChild(splash);
  window.setTimeout(function () {
    if (splash.parentNode) splash.parentNode.removeChild(splash);
  }, 3500);
})();
