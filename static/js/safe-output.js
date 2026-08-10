(function () {
  'use strict';

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>'"]/g, function (character) {
      return {
        '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
      }[character];
    });
  }

  function setText(element, value) {
    if (element) element.textContent = value == null ? '' : String(value);
  }

  window.SafeOutput = Object.freeze({ escapeHtml: escapeHtml, setText: setText });
}());
