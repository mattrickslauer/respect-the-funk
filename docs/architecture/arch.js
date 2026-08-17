/* arch.js — drawing helpers for the architecture pages.
 *
 * SVG has no text flow, so every line break in this document is a number
 * somebody has to get right. Rather than get them right by eye — which is how
 * the deck in `content/deck` first shipped fourteen clipped captions — the box
 * helper wraps on a measured character budget. The face is monospace, so the
 * budget is exact rather than approximate: IBM Plex Mono advances at 0.6em, plus
 * whatever letter-spacing the class adds.
 *
 * Everything here is deterministic. Re-running the build has to produce a
 * byte-identical page or the PDF silently drifts from the one that was reviewed.
 */
(function (root) {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';

  /** Advance per character, by text class. Keep in step with arch.css. */
  var CHAR = {
    't-lbl':  20 * 0.6 + 20 * 0.14,
    't-val':  26 * 0.6,
    't-mono': 21 * 0.6,
    't-sm':   17 * 0.6,
    't-code': 20 * 0.6
  };

  function charW(cls) {
    var base = (cls || 't-mono').split(' ')[0];
    return CHAR[base] || CHAR['t-mono'];
  }

  function el(name, attrs, parent) {
    var n = document.createElementNS(NS, name);
    if (attrs) for (var k in attrs) {
      if (attrs[k] !== null && attrs[k] !== undefined) n.setAttribute(k, attrs[k]);
    }
    if (parent) parent.appendChild(n);
    return n;
  }

  function text(svg, x, y, s, cls, anchor) {
    var t = el('text', { x: x, y: y, class: cls || 't-mono', 'text-anchor': anchor || 'start',
                         'xml:space': 'preserve' }, svg);
    t.textContent = s;
    return t;
  }

  /** Greedy wrap to a character budget. Long single words are left alone —
   *  breaking `one_open_thread_per_counterparty` would be worse than running on. */
  function wrap(s, max) {
    var out = [], line = '';
    s.split(' ').forEach(function (w) {
      var next = line ? line + ' ' + w : w;
      if (next.length > max && line) { out.push(line); line = w; }
      else line = next;
    });
    if (line) out.push(line);
    return out;
  }

  /** How many characters of `cls` fit across `w` px with `pad` each side. */
  function budget(w, cls, pad) {
    return Math.floor((w - 2 * (pad === undefined ? 18 : pad)) / charW(cls));
  }

  var TONE = {
    live: { stroke: 'var(--rule-hi)', fill: 'var(--panel)',   title: 't-lbl' },
    hot:  { stroke: 'var(--phosphor)', fill: 'rgba(217,127,74,.10)', title: 't-lbl on', w: 3 },
    crdb: { stroke: 'var(--crdb)', fill: 'rgba(125,77,255,.10)', title: 't-lbl crd', w: 3 },
    good: { stroke: 'var(--carrier)', fill: 'var(--panel)', title: 't-lbl gd' },
    cut:  { stroke: 'var(--cut)', fill: 'rgba(240,67,47,.07)', title: 't-lbl bd' },
    off:  { stroke: 'var(--rule-hi)', fill: 'transparent', title: 't-lbl', dash: '10 10' },
    bare: { stroke: 'var(--rule)', fill: 'transparent', title: 't-lbl' }
  };

  /**
   * A labelled box.
   *
   * opt: { x, y, w, h, k, lines: [string | {t, cls}], tone, pad, lead, chip }
   * Returns the box's height, so a caller stacking boxes can let the content
   * decide. Pass `h` to fix it instead.
   */
  function box(svg, opt) {
    var tone = TONE[opt.tone || 'live'];
    var pad = opt.pad === undefined ? 18 : opt.pad;
    var g = el('g', { class: 'box' }, svg);

    // Lay the text out first so an unfixed height can follow the content.
    // A chip sits on the title row at the right-hand end, and the first body
    // line is full width — so without this the two cross. Reserve the row.
    var rows = [], y = (opt.k ? 34 : 8) + pad + (opt.chip ? 14 : 0);
    (opt.lines || []).forEach(function (ln) {
      var t = typeof ln === 'string' ? ln : ln.t;
      var cls = typeof ln === 'string' ? 't-mono' : (ln.cls || 't-mono');
      var lead = (typeof ln === 'object' && ln.lead) || opt.lead || 27;
      wrap(t, budget(opt.w, cls, pad)).forEach(function (line) {
        rows.push({ t: line, cls: cls, dy: y });
        y += lead;
      });
      y += (typeof ln === 'object' && ln.gap) ? ln.gap : 0;
    });
    var h = Math.max(opt.h || 0, y + pad - 8, 60);

    el('rect', {
      x: opt.x, y: opt.y, width: opt.w, height: h, rx: 10,
      fill: tone.fill, stroke: tone.stroke,
      'stroke-width': tone.w || 2,
      'stroke-dasharray': tone.dash || null
    }, g);

    if (opt.k) text(g, opt.x + pad, opt.y + pad + 16, opt.k, tone.title);
    rows.forEach(function (r) { text(g, opt.x + pad, opt.y + r.dy, r.t, r.cls); });

    if (opt.chip) {
      var cw = opt.chip.length * charW('t-sm') + 22;
      el('rect', { x: opt.x + opt.w - cw - pad, y: opt.y + pad - 2, width: cw, height: 26, rx: 6,
                   fill: 'transparent', stroke: 'var(--warn)', 'stroke-width': 1.6 }, g);
      text(g, opt.x + opt.w - cw - pad + 11, opt.y + pad + 16, opt.chip, 't-sm wn');
    }
    return h;
  }

  /** A straight or elbowed run with a solid head. `d` is any path string. */
  function wire(svg, d, cls) {
    return el('path', { d: d, class: 'wire ' + (cls || '') }, svg);
  }

  function head(svg, x, y, dir, cls) {
    var a = { r: 0, l: Math.PI, d: Math.PI / 2, u: -Math.PI / 2 }[dir || 'r'];
    var c = cls && cls.indexOf('crdb') > -1 ? 'var(--crdb)'
          : cls && cls.indexOf('good') > -1 ? 'var(--carrier)'
          : cls && cls.indexOf('bad') > -1 ? 'var(--cut)'
          : cls && cls.indexOf('warn') > -1 ? 'var(--warn)'
          : cls && cls.indexOf('faint') > -1 ? 'var(--ember)'
          : cls && cls.indexOf('rule') > -1 ? 'var(--rule-hi)'
          : 'var(--phosphor)';
    var p = [[x, y],
             [x - Math.cos(a - 0.42) * 15, y - Math.sin(a - 0.42) * 15],
             [x - Math.cos(a + 0.42) * 15, y - Math.sin(a + 0.42) * 15]];
    return el('path', { d: 'M' + p[0] + 'L' + p[1] + 'L' + p[2] + 'Z', fill: c }, svg);
  }

  /** Straight arrow between two points, head included. */
  function arrow(svg, x0, y0, x1, y1, cls) {
    var dir = x1 === x0 ? (y1 > y0 ? 'd' : 'u') : (x1 > x0 ? 'r' : 'l');
    var back = 13;
    var ex = x1, ey = y1;
    if (dir === 'r') ex -= back; if (dir === 'l') ex += back;
    if (dir === 'd') ey -= back; if (dir === 'u') ey += back;
    wire(svg, 'M' + x0 + ' ' + y0 + 'L' + ex + ' ' + ey, cls);
    head(svg, x1, y1, dir, cls);
  }

  /** A section rule with a title, for dividing a page into bands. */
  function band(svg, x, y, w, title, cls) {
    text(svg, x, y, title, cls || 't-lbl');
    wire(svg, 'M' + (x + title.length * charW('t-lbl') + 20) + ' ' + (y - 7) + 'H' + (x + w), 'rule thin');
  }

  root.A = { el: el, text: text, wrap: wrap, budget: budget, charW: charW,
             box: box, wire: wire, arrow: arrow, head: head, band: band };
})(window);
