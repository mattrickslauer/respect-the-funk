/* scene.js — the deterministic runtime every overlay scene is built on.
 *
 * The one rule that matters: NOTHING here animates itself. No CSS keyframes, no
 * requestAnimationFrame, no transitions. A scene is a pure function of time —
 * `frame(t)` puts every element exactly where it belongs at t seconds and nowhere
 * else. The renderer then walks t in fixed steps and screenshots each one.
 *
 * That is not a stylistic preference. A CSS animation captured by a screenshotter
 * lands wherever the compositor happened to be, so two renders of the same scene
 * differ and a re-render after a tweak jitters against the take you already cut.
 * Driving everything from an explicit clock makes a frame reproducible forever,
 * which is what lets the editor re-render scene 12 at 2am without recutting.
 *
 * The seeded RNG exists for the same reason. The groove field is scattered, and a
 * scatter that moves between renders is a scatter you cannot cut around.
 */

/* Wrapped in an IIFE, and not for tidiness: classic scripts share one global
 * lexical scope, so a bare `const lerp` here and `const { lerp } = SP` in a scene
 * are a redeclaration error that kills the page before it can render. Everything
 * a scene needs leaves through `window.SP` at the bottom and nothing else does. */
(function () {

const NS = 'http://www.w3.org/2000/svg';

/* ---- maths ------------------------------------------------------------- */

/** Mulberry32. Small, fast, and — the only property we need — identical every run. */
function rng(seed) {
  let a = seed | 0;
  return function () {
    a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);
const lerp = (a, b, u) => a + (b - a) * u;

/** Progress through a window that opens at `start` and lasts `dur`. */
const win = (t, start, dur) => clamp01((t - start) / dur);

const ease = {
  linear: (u) => u,
  in: (u) => u * u * u,
  out: (u) => 1 - Math.pow(1 - u, 3),
  inOut: (u) => (u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2),
  expo: (u) => (u === 1 ? 1 : 1 - Math.pow(2, -10 * u)),
  /** Overshoots once and settles. Used only where something *arrives*. */
  back: (u) => {
    const c = 1.70158, d = c + 1;
    return 1 + d * Math.pow(u - 1, 3) + c * Math.pow(u - 1, 2);
  },
};

/* ---- polar -------------------------------------------------------------
 * Every scene is laid out on a disc rather than a grid, because the mark is a
 * record read as a spectrum and the diagrams should look like they came out of
 * the same drawing. Rectangles are available and deliberately unused.
 */

const CX = 960, CY = 540;

/** Degrees, clockwise, 0 = twelve o'clock — the way you'd describe a groove. */
function polar(r, deg, cx = CX, cy = CY) {
  const a = (deg - 90) * Math.PI / 180;
  return { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) };
}

/** Arc path along a single radius. `sweep` may exceed 180 safely. */
function arc(r, a0, a1, cx = CX, cy = CY) {
  const p0 = polar(r, a0, cx, cy), p1 = polar(r, a1, cx, cy);
  const large = Math.abs(a1 - a0) > 180 ? 1 : 0;
  const dir = a1 > a0 ? 1 : 0;
  return `M ${p0.x.toFixed(2)} ${p0.y.toFixed(2)} A ${r} ${r} 0 ${large} ${dir} ${p1.x.toFixed(2)} ${p1.y.toFixed(2)}`;
}

/** Filled wedge between two radii — the "filtered subspace" shape. */
function wedge(r0, r1, a0, a1, cx = CX, cy = CY) {
  const o0 = polar(r1, a0, cx, cy), o1 = polar(r1, a1, cx, cy);
  const i1 = polar(r0, a1, cx, cy), i0 = polar(r0, a0, cx, cy);
  const large = Math.abs(a1 - a0) > 180 ? 1 : 0;
  return `M ${o0.x.toFixed(2)} ${o0.y.toFixed(2)} A ${r1} ${r1} 0 ${large} 1 ${o1.x.toFixed(2)} ${o1.y.toFixed(2)}`
       + ` L ${i1.x.toFixed(2)} ${i1.y.toFixed(2)} A ${r0} ${r0} 0 ${large} 0 ${i0.x.toFixed(2)} ${i0.y.toFixed(2)} Z`;
}

/* ---- dom ---------------------------------------------------------------- */

function el(tag, attrs = {}, parent = null) {
  const n = document.createElementNS(NS, tag);
  for (const k in attrs) n.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(n);
  return n;
}

/** Type. Always mono, always tracked out — see lib/scene.css for why. */
function label(text, x, y, opts = {}, parent) {
  const n = el('text', {
    x, y,
    'class': 'lbl' + (opts.cls ? ' ' + opts.cls : ''),
    'text-anchor': opts.anchor || 'middle',
    'font-size': opts.size || 26,
  }, parent);
  n.textContent = text;
  return n;
}

/** A soft dark ellipse laid under type so it survives a bright desk behind it.
 *  The only concession in the whole system to not knowing what is underneath. */
function scrim(x, y, rx, ry, parent) {
  return el('ellipse', { cx: x, cy: y, rx, ry, fill: 'url(#scrimGrad)' }, parent);
}

/* ---- the groove field --------------------------------------------------
 * The recurring character. Counterparties as dots on concentric grooves, so the
 * index literally looks like a record. Built once with a fixed seed; scenes only
 * ever change how each dot is *lit*, never where it sits.
 */

function grooveField(parent, opts = {}) {
  const rings = opts.rings || 9;
  const r0 = opts.r0 || 120, r1 = opts.r1 || 430;
  const rand = rng(opts.seed || 7);
  const g = el('g', { class: 'field' }, parent);
  const dots = [];

  for (let i = 0; i < rings; i++) {
    const rr = lerp(r0, r1, i / (rings - 1));
    // Denser rings further out, so the disc reads as evenly filled rather than
    // crowded at the centre the way equal-count rings would.
    const n = Math.round(10 + 5.2 * i);
    const off = rand() * 360;
    for (let j = 0; j < n; j++) {
      const deg = off + (360 * j) / n + (rand() - 0.5) * 5;
      const jr = rr + (rand() - 0.5) * 14;
      const p = polar(jr, deg);
      const node = el('circle', { cx: p.x.toFixed(2), cy: p.y.toFixed(2), r: 3.4, class: 'dot' }, g);
      // style, not attributes — see the note in scene.css on why .dot is unstyled.
      node.style.fill = 'var(--faint)';
      node.style.opacity = '0.34';
      dots.push({ node, deg: ((deg % 360) + 360) % 360, r: jr, ring: i, rand: rand() });
    }
  }
  return { g, dots };
}

/* ---- registry ----------------------------------------------------------- */

const Scene = {
  dur: 6,
  _frame: null,
  define(cfg) {
    this.dur = cfg.dur;
    this._frame = cfg.frame;
    const root = document.getElementById('stage');
    cfg.setup(root);
    window.SCENE_DURATION = cfg.dur;
    window.seek = (t) => { cfg.frame(Math.max(0, Math.min(cfg.dur, t))); };
    window.seek(0);
    window.SCENE_READY = true;
  },
};

window.SP = { rng, clamp01, lerp, win, ease, polar, arc, wedge, el, label, scrim, grooveField, Scene, CX, CY };

})();
