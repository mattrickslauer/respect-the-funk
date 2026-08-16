/* defs.js — the shared <defs> every scene needs, injected rather than copy-pasted.
 *
 * Two of these exist purely because the plate is transparent and we do not control
 * what ends up behind it:
 *
 *   #lift    a soft dark drop-shadow on everything. Over a bright desk this is the
 *            difference between a diagram and a smudge. It is deliberately not a
 *            glow — a glow reads as sci-fi and this is an instrument, not a HUD.
 *   #scrimGrad  a radial fade to near-black, laid under type only.
 *
 * #groove is the faint concentric guide that makes the disc read as a record
 * rather than as a scatter plot. It is the quietest thing on screen and it is what
 * ties all thirteen scenes to the mark.
 */
(function () {
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.getElementById('svg');
  const defs = document.createElementNS(NS, 'defs');
  defs.innerHTML = `
    <filter id="lift" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="2" stdDeviation="6"
                    flood-color="#060504" flood-opacity="0.55"/>
    </filter>

    <!-- Deliberately weak. The .lbl rule already paints a dark rim around every
         glyph, which does most of the legibility work; at 0.72 this read as a
         black blob sitting behind the type rather than as insurance under it. -->
    <radialGradient id="scrimGrad">
      <stop offset="0%"   stop-color="#060504" stop-opacity="0.42"/>
      <stop offset="55%"  stop-color="#060504" stop-opacity="0.24"/>
      <stop offset="100%" stop-color="#060504" stop-opacity="0"/>
    </radialGradient>

    <radialGradient id="discWash">
      <stop offset="0%"   stop-color="#d97f4a" stop-opacity="0.10"/>
      <stop offset="100%" stop-color="#d97f4a" stop-opacity="0"/>
    </radialGradient>

    <linearGradient id="crdbSweep" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%"   stop-color="#7d4dff" stop-opacity="0"/>
      <stop offset="50%"  stop-color="#a583ff" stop-opacity="1"/>
      <stop offset="100%" stop-color="#7d4dff" stop-opacity="0"/>
    </linearGradient>
  `;
  svg.insertBefore(defs, svg.firstChild);

  // The groove guide — nine faint rings the dots sit on.
  const g = document.createElementNS(NS, 'g');
  g.setAttribute('id', 'grooves');
  for (let i = 0; i < 9; i++) {
    const r = 120 + (430 - 120) * (i / 8);
    const c = document.createElementNS(NS, 'circle');
    c.setAttribute('cx', 960); c.setAttribute('cy', 540); c.setAttribute('r', r.toFixed(1));
    c.setAttribute('fill', 'none');
    c.setAttribute('stroke', '#8c8175');
    c.setAttribute('stroke-width', '1.5');
    c.setAttribute('opacity', '0.13');
    g.appendChild(c);
  }
  document.getElementById('stage').appendChild(g);
  window.GROOVES = g;
})();
