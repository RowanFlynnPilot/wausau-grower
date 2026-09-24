// WCAG 2.1 text-contrast audit for The Wausau Grower.
// Walks every visible element that directly contains text, resolves the
// effective background by compositing ancestor backgrounds (alpha-aware), and
// returns the elements below AA: 4.5:1 for normal text, 3:1 for large text
// (>= 24px, or >= 18.66px at weight >= 700). Text drawn over an image or SVG
// (a background-image ancestor before an opaque color) is skipped rather than
// guessed. Loaded by tests/smoke_test.py; also pasteable into a console.
(() => {
  function parse(c) {
    if (!c || c === 'transparent') return { r: 0, g: 0, b: 0, a: 0 };
    let m = c.match(/^rgba?\(([^)]+)\)$/);
    if (m) {
      const p = m[1].split(/[\s,\/]+/).filter(Boolean).map(Number);
      return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
    }
    m = c.match(/^color\(srgb ([^)]+)\)$/);
    if (m) {
      const p = m[1].split(/[\s\/]+/).filter(Boolean).map(Number);
      return { r: p[0] * 255, g: p[1] * 255, b: p[2] * 255, a: p.length > 3 ? p[3] : 1 };
    }
    return null;
  }
  const over = (top, bot) => {
    const a = top.a + bot.a * (1 - top.a);
    if (!a) return { r: 0, g: 0, b: 0, a: 0 };
    const mix = k => (top[k] * top.a + bot[k] * bot.a * (1 - top.a)) / a;
    return { r: mix('r'), g: mix('g'), b: mix('b'), a };
  };
  const lum = c => {
    const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };

  function effectiveBg(el) {
    const layers = [];
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const cs = getComputedStyle(n);
      const bg = parse(cs.backgroundColor);
      if (cs.backgroundImage && cs.backgroundImage !== 'none' && (!bg || bg.a < 1)) return null;
      if (bg && bg.a > 0) layers.push(bg);
      if (bg && bg.a >= 1) break;
    }
    let acc = { r: 255, g: 255, b: 255, a: 1 };
    for (let i = layers.length - 1; i >= 0; i--) acc = over(layers[i], acc);
    return acc;
  }
  function overSvg(el) {
    for (let n = el.parentElement; n; n = n.parentElement) {
      if (n.classList && (n.classList.contains('art') || n.classList.contains('photo') || n.classList.contains('modal-art'))) return true;
    }
    return false;
  }
  function visible(el) {
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }
  const label = el => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
    (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).join('.') : '');

  window.__contrastAudit = function (scope) {
    const root = scope ? document.querySelector(scope) : document.body;
    const fails = [];
    for (const el of root.querySelectorAll('*')) {
      if (['SCRIPT', 'STYLE', 'SVG', 'svg', 'NOSCRIPT', 'OPTION'].includes(el.tagName)) continue;
      if (el.closest('svg') || el.closest('[aria-hidden="true"]')) continue;
      const own = [...el.childNodes].filter(n => n.nodeType === 3 && n.textContent.trim()).map(n => n.textContent.trim()).join(' ');
      if (!own || !visible(el)) continue;
      const cs = getComputedStyle(el);
      let fg = parse(cs.color);
      if (!fg) continue;
      const bg = effectiveBg(el);
      if (!bg) continue;
      // element opacity blends the text toward what's behind it
      let op = 1; for (let n = el; n; n = n.parentElement) op *= +getComputedStyle(n).opacity;
      fg = over({ ...fg, a: fg.a * op }, bg);
      const size = parseFloat(cs.fontSize), weight = parseInt(cs.fontWeight, 10) || 400;
      const large = size >= 24 || (size >= 18.66 && weight >= 700);
      // a lone symbol (★ ✕ ♥) is an icon: WCAG 1.4.11 non-text contrast, 3:1
      const glyph = [...own].length === 1 && !/[\p{L}\p{N}]/u.test(own);
      const need = large || glyph ? 3 : 4.5, got = ratio(fg, bg);
      if (got + 0.005 < need) fails.push({ el: label(el), text: own.slice(0, 40), ratio: +got.toFixed(2), need, overArt: overSvg(el) });
    }
    return fails;
  };
})();
