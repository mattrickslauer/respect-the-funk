/* deck.js — the small amount of drawing that is not worth writing by hand.
 *
 * One object recurs through this deck the way it recurs through the video: a disc
 * of counterparties arranged in concentric grooves. It is the brand mark read as
 * a record, and it is the only illustration that repeats — established on the
 * problem slide, filtered on the index slide, filled by the fleet, rewound at the
 * end. Thirteen unrelated pictures would have been easier and would have said
 * nothing.
 *
 * Every ring's dots are placed deterministically. There is no randomness anywhere
 * in here on purpose: a re-render has to produce the identical PNG, or the deck
 * silently drifts between the version that was reviewed and the version that
 * shipped.
 */
(function (root) {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';

  function el(name, attrs, parent) {
    var n = document.createElementNS(NS, name);
    if (attrs) for (var k in attrs) if (attrs[k] !== null && attrs[k] !== undefined) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }

  /** Degrees, clockwise, 0 at twelve o'clock — the way a record is described. */
  function polar(cx, cy, r, deg) {
    var a = (deg - 90) * Math.PI / 180;
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  }

  function arc(cx, cy, r, a0, a1) {
    var p0 = polar(cx, cy, r, a0), p1 = polar(cx, cy, r, a1);
    var large = Math.abs(a1 - a0) > 180 ? 1 : 0;
    var sweep = a1 > a0 ? 1 : 0;
    return 'M' + p0[0].toFixed(2) + ' ' + p0[1].toFixed(2) +
           'A' + r + ' ' + r + ' 0 ' + large + ' ' + sweep + ' ' + p1[0].toFixed(2) + ' ' + p1[1].toFixed(2);
  }

  /** A filled wedge between two angles, from inner radius to outer. */
  function wedge(cx, cy, r0, r1, a0, a1) {
    var o0 = polar(cx, cy, r1, a0), o1 = polar(cx, cy, r1, a1);
    var i1 = polar(cx, cy, r0, a1), i0 = polar(cx, cy, r0, a0);
    var large = Math.abs(a1 - a0) > 180 ? 1 : 0;
    return 'M' + o0 + 'A' + r1 + ' ' + r1 + ' 0 ' + large + ' 1 ' + o1 +
           'L' + i1 + 'A' + r0 + ' ' + r0 + ' 0 ' + large + ' 0 ' + i0 + 'Z';
  }

  /**
   * The groove field.
   *
   * `paint(i, ringIndex, deg)` returns {fill, r, opacity} for one counterparty, or
   * null to leave the position empty. Returning a colour per dot rather than per
   * ring is what lets the same call draw "everyone", "the shortlist" and "the ones
   * we have learned something about" without three different functions.
   */
  function disc(svg, opt) {
    var cx = opt.cx, cy = opt.cy;
    var g = el('g', null, svg);
    var rings = opt.rings || [];
    var i = 0;

    rings.forEach(function (ring, ri) {
      if (opt.grooves !== false) {
        el('circle', {
          cx: cx, cy: cy, r: ring.r, fill: 'none',
          stroke: opt.grooveColor || 'var(--rule)', 'stroke-width': 2
        }, g);
      }
      var n = ring.n;
      var off = ring.off === undefined ? (ri * 7) : ring.off;
      for (var k = 0; k < n; k++) {
        var deg = off + (360 / n) * k;
        var spec = opt.paint ? opt.paint(i, ri, deg) : { fill: 'var(--dimmer)', r: 5 };
        i++;
        if (!spec) continue;
        var p = polar(cx, cy, ring.r, deg);
        el('circle', {
          cx: p[0].toFixed(2), cy: p[1].toFixed(2),
          r: spec.r === undefined ? 5 : spec.r,
          fill: spec.fill, opacity: spec.opacity === undefined ? 1 : spec.opacity
        }, g);
      }
    });
    return g;
  }

  /** The spindle hole — the mark's own centre. Every polar diagram has one. */
  function hub(svg, cx, cy, opt) {
    opt = opt || {};
    var g = el('g', null, svg);
    el('circle', { cx: cx, cy: cy, r: opt.r || 26, fill: 'none',
                   stroke: opt.stroke || 'var(--phosphor)', 'stroke-width': opt.w || 3 }, g);
    if (opt.dot) el('circle', { cx: cx, cy: cy, r: 7, fill: opt.stroke || 'var(--phosphor)' }, g);
    return g;
  }

  function text(svg, x, y, s, cls, anchor) {
    var t = el('text', { x: x, y: y, class: cls || 't-lbl', 'text-anchor': anchor || 'start' }, svg);
    t.textContent = s;
    return t;
  }

  /** A straight run with a solid head, for the few places a flow is not circular. */
  function arrow(svg, x0, y0, x1, y1, cls) {
    var g = el('g', null, svg);
    var a = Math.atan2(y1 - y0, x1 - x0);
    var hx = x1 - Math.cos(a) * 16, hy = y1 - Math.sin(a) * 16;
    el('path', { d: 'M' + x0 + ' ' + y0 + 'L' + hx + ' ' + hy, class: 'wire ' + (cls || '') }, g);
    var p1 = [x1, y1];
    var p2 = [x1 - Math.cos(a - 0.42) * 22, y1 - Math.sin(a - 0.42) * 22];
    var p3 = [x1 - Math.cos(a + 0.42) * 22, y1 - Math.sin(a + 0.42) * 22];
    el('path', { d: 'M' + p1 + 'L' + p2 + 'L' + p3 + 'Z',
                 fill: 'currentColor', class: (cls || '').replace('wire', '') }, g);
    g.setAttribute('color', cls && cls.indexOf('crdb') > -1 ? 'var(--crdb)'
                          : cls && cls.indexOf('good') > -1 ? 'var(--carrier)'
                          : cls && cls.indexOf('bad') > -1 ? 'var(--cut)'
                          : cls && cls.indexOf('faint') > -1 ? 'var(--ember)'
                          : 'var(--phosphor)');
    return g;
  }

  root.Deck = { el: el, polar: polar, arc: arc, wedge: wedge, disc: disc, hub: hub, text: text, arrow: arrow, NS: NS };
})(window);
