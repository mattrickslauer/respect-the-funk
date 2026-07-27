/* The renderer.
 *
 * One recursive walk over the resolved model. There is no per-screen code anywhere in
 * this file and there must never be: every screen is data, and the only thing that
 * varies between them is the tree they carry. Adding a screen touches no JavaScript.
 *
 * Adding a *primitive* touches exactly two places — a declaration in
 * spec/primitives.json, so the build can validate it, and an entry in DRAW below, so
 * it can be drawn. Nothing else in the system needs to know it exists.
 */
(function () {
  "use strict";

  var MODEL = null;
  var state = { view: null, annotations: true };

  /* -------------------------------------------------------------- dom helpers */
  function el(tag, props) {
    var node = document.createElement(tag);
    props = props || {};
    Object.keys(props).forEach(function (key) {
      var value = props[key];
      if (value === null || value === undefined || value === false) return;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "style") node.setAttribute("style", value);
      else if (key.slice(0, 2) === "on") node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    });
    for (var i = 2; i < arguments.length; i++) add(node, arguments[i]);
    return node;
  }

  function add(parent, child) {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) { child.forEach(function (c) { add(parent, c); }); return; }
    parent.appendChild(child.nodeType ? child : document.createTextNode(String(child)));
  }

  function bars(n, label) {
    var rows = [];
    for (var i = 0; i < (n || 1); i++) rows.push(el("div", { class: "bar" }));
    return el("div", { class: "ph" },
      label ? el("div", { class: "ph__label", text: label }) : null,
      el("div", { class: "bars" }, rows));
  }

  function hatch(ratio, note) {
    var parts = String(ratio || "16:9").split(":");
    var pct = (Number(parts[1]) / Number(parts[0])) * 100;
    return el("div", {
      class: "hatch",
      style: "aspect-ratio:" + parts[0] + "/" + parts[1] + ";min-height:" + Math.min(pct, 60) + "px"
    }, note ? el("span", { class: "hatch__note", text: note }) : null);
  }

  function fieldsOf(node) { return node.binding ? node.binding.fields : []; }

  /* -------------------------------------------------------------- primitives */
  var DRAW = {
    col: function (n) { return el("div", { class: "b b--col " + gap(n) }, kids(n)); },
    row: function (n) {
      var weights = n.weights || [];
      var box = el("div", { class: "b b--row " + gap(n) });
      (n.children || []).forEach(function (child, i) {
        var wrapped = el("div", { class: "b", style: "flex:" + (weights[i] || 1) + " 1 0;min-width:0" }, draw(child));
        box.appendChild(wrapped);
      });
      return box;
    },
    grid: function (n) {
      return el("div", {
        class: "b b--grid " + gap(n),
        style: "grid-template-columns:repeat(auto-fit,minmax(" + (n.min || 200) + "px,1fr))"
      }, kids(n));
    },

    region: function (n) {
      return el("section", { class: "region" },
        el("header", { class: "region__head" },
          el("h2", { class: "region__title", text: n.title || "Region" }),
          n.hint ? el("p", { class: "region__hint", text: n.hint }) : null),
        el("div", { class: "region__body" }, kids(n)));
    },

    card: function (n) {
      return el("div", { class: "card" },
        n.title ? el("div", { class: "card__title", text: n.title }) : null,
        el("div", { class: "card__body" }, kids(n)));
    },

    table: function (n) {
      var fields = fieldsOf(n);
      var head = el("tr", {}, fields.map(function (f) {
        return el("th", {}, f.name, el("div", { class: "t__kind", text: f.kind }));
      }));
      var body = [];
      for (var r = 0; r < (n.rows || 3); r++) {
        body.push(el("tr", {}, fields.map(function (f) {
          return el("td", {}, el("div", { class: "cell " + cellMod(f.kind) }));
        })));
      }
      return el("div", { class: "tablewrap" },
        el("table", { class: "t" }, el("thead", {}, head), el("tbody", {}, body)));
    },

    form: function (n) {
      return el("div", { class: "b b--col gap-base" },
        el("div", { class: "fields fields--" + (n.layout || "stacked") },
          fieldsOf(n).map(function (f) {
            return el("label", { class: "field" },
              el("span", { class: "field__label" },
                f.name,
                f.required ? el("span", { class: "field__req", text: "required" }) : null,
                el("span", { class: "field__type", text: f.kind })),
              el("div", { class: "input input--" + f.kind }));
          })),
        n.submit ? el("div", { class: "actions" }, el("span", { class: "btn btn--primary", text: n.submit })) : null);
    },

    detail: function (n) {
      return el("div", {
        class: "detail",
        style: "grid-template-columns:repeat(" + (n.columns || 2) + ",minmax(0,1fr))"
      }, fieldsOf(n).map(function (f) {
        return el("div", { class: "detail__row" },
          el("span", { class: "detail__key", text: f.name }),
          el("div", { class: "detail__val" }));
      }));
    },

    repeat: function (n) {
      var out = [el("div", { class: "repeat__mark", text: "repeats per " + (n.of || "record") + " — " + (n.count || 3) + " shown" })];
      for (var i = 0; i < (n.count || 3); i++) out.push(kids(n));
      return el("div", { class: "repeat" }, out);
    },

    gallery: function (n) {
      var tiles = [];
      for (var i = 0; i < (n.count || 4); i++) tiles.push(hatch(n.ratio || "9:16", n.ratio || null));
      return el("div", { class: "b b--col gap-tight" },
        el("div", { class: "gallery" }, tiles),
        n.caption ? el("div", { class: "ph__label", text: n.caption }) : null);
    },

    media: function (n) { return hatch(n.ratio || "16:9", (n.kind || "media") + (n.caption ? " — " + n.caption : "")); },

    stats: function (n) {
      return el("div", { class: "stats" }, (n.items || []).map(function (item) {
        return el("div", { class: "stat" },
          el("div", { class: "stat__val" }),
          el("div", { class: "stat__label", text: item.label }),
          el("div", { class: "stat__src", text: item.source }));
      }));
    },

    actions: function (n) {
      return el("div", { class: "actions" + (n.align === "right" ? " actions--right" : "") },
        (n.items || []).map(function (item) {
          return el("span", { class: "btn" + (item.kind ? " btn--" + item.kind : ""), text: item.label });
        }));
    },

    tabs: function (n) {
      return el("div", { class: "b b--col gap-base" },
        el("div", { class: "actions" }, (n.items || []).map(function (t, i) {
          return el("span", { class: "btn" + (i === 0 ? " btn--primary" : ""), text: t.label });
        })),
        (n.items && n.items[0] ? (n.items[0].children || []).map(draw) : null));
    },

    filters: function (n) {
      return el("div", { class: "actions" }, fieldsOf(n).map(function (f) {
        return el("span", { class: "btn", text: f.name + " ▾" });
      }));
    },

    banner: function (n) {
      var tone = n.tone || "info";
      return el("div", { class: "banner banner--" + tone },
        el("div", { class: "banner__tone", text: tone }),
        el("div", { class: "banner__src", text: n.source || "" }));
    },

    empty: function (n) {
      return el("div", { class: "empty" },
        el("div", { class: "empty__msg", text: n.message || "Nothing here yet." }),
        n.action ? el("span", { class: "btn btn--primary", text: n.action }) : null);
    },

    text: function (n) { return bars(n.lines || 2, n.label); },

    slot: function (n) {
      return el("div", { class: "slot" },
        el("div", { class: "slot__name", text: "unresolved — " + (n.name || "slot") }),
        n.reason ? el("div", { class: "slot__reason", text: n.reason }) : null);
    }
  };

  function gap(n) { return "gap-" + (n.gap || "base"); }
  function kids(n) { return (n.children || []).map(draw); }
  function cellMod(kind) {
    if (kind === "number") return "cell--num";
    if (kind === "enum") return "cell--enum";
    if (kind === "bool") return "cell--bool";
    return "";
  }

  /* -------------------------------------------------------------- the walk */
  function draw(node) {
    var render = DRAW[node.type];
    if (!render) {
      // The build rejects unknown primitives, so this only fires if the two files
      // drift. Say which one is behind rather than rendering nothing.
      return el("div", { class: "slot" },
        el("div", { class: "slot__name", text: "no renderer for “" + node.type + "”" }),
        el("div", { class: "slot__reason", text: "Declared in primitives.json but missing from DRAW in renderer.js." }));
    }
    var box = el("div", { class: "b" }, render(node));
    var label = node.type + (node.binding ? " · " + node.binding.entity : "");
    box.appendChild(el("span", { class: "tag", text: label }));
    if (node.note) box.appendChild(el("div", { class: "note", text: node.note }));
    return box;
  }

  /* -------------------------------------------------------------- views */
  function screenView(screen) {
    return el("div", {},
      el("div", { class: "titleblock" },
        el("div", { class: "titleblock__body" },
          el("h1", { class: "titleblock__name", text: screen.title }),
          screen.purpose ? el("p", { class: "titleblock__purpose", text: screen.purpose }) : null),
        el("div", { class: "titleblock__meta" },
          metaRow("route", screen.route),
          metaRow("entity", screen.entity || "—"),
          metaRow("spec", "spec/screens/" + screen.id + ".json"))),
      el("div", { class: "sheet" }, draw(screen.body)));
  }

  function metaRow(key, value) {
    return el("div", { class: "titleblock__row" },
      el("span", { class: "titleblock__key", text: key }),
      el("span", { class: "titleblock__val", text: value }));
  }

  function primitivesView() {
    var names = Object.keys(MODEL.primitives).sort();
    return el("div", {},
      el("div", { class: "titleblock" },
        el("div", { class: "titleblock__body" },
          el("h1", { class: "titleblock__name", text: "Vocabulary" }),
          el("p", { class: "titleblock__purpose", text: "Every block on every screen is one of these. The build refuses a node that names anything else, or that carries a prop its primitive does not declare." })),
        el("div", { class: "titleblock__meta" },
          metaRow("primitives", names.length),
          metaRow("nodes drawn", MODEL.stats.nodes),
          metaRow("spec", "spec/primitives.json"))),
      el("div", { class: "sheet" },
        el("div", { class: "ref" }, names.map(function (name) {
          var p = MODEL.primitives[name];
          var used = MODEL.stats.by_type[name] || 0;
          return el("div", { class: "ref__item" },
            el("div", { class: "ref__name", text: name }),
            el("div", { class: "ref__summary", text: p.summary }),
            el("div", { class: "ref__props", text: "props: " + Object.keys(p.props).join(", ") + "   ·   used " + used + "×" }));
        }))));
  }

  function entitiesView() {
    var names = Object.keys(MODEL.entities).sort();
    return el("div", {},
      el("div", { class: "titleblock" },
        el("div", { class: "titleblock__body" },
          el("h1", { class: "titleblock__name", text: "Model" }),
          el("p", { class: "titleblock__purpose", text: "Read from the application's domain model at build time, not restated here. Every table, form and readout in this wireframe draws its fields from this registry, so a field added to the model appears on the screens that bind it." })),
        el("div", { class: "titleblock__meta" },
          metaRow("entities", MODEL.stats.entities),
          metaRow("source", MODEL.meta.model_source),
          metaRow("read via", "ast, not import"))),
      el("div", { class: "sheet" },
        el("div", { class: "ref" }, names.map(function (name) {
          var e = MODEL.entities[name];
          return el("div", { class: "ref__item" },
            el("div", { class: "ref__name", text: name + (e.enum ? "  (enum)" : "") }),
            e.enum
              ? el("div", { class: "chips" }, (e.values || []).map(function (v) {
                  return el("span", { class: "chip", text: v });
                }))
              : el("div", { class: "chips" }, e.fields.map(function (f) {
                  var cls = "chip" + (f.required ? " chip--req" : "") + (f.inherited ? " chip--inh" : "");
                  return el("span", { class: cls, text: f.name + " : " + f.kind });
                })));
        }))));
  }

  /* -------------------------------------------------------------- shell */
  function rail() {
    var nav = el("nav", { class: "rail" },
      el("div", { class: "rail__mark" },
        el("div", { class: "rail__product", text: MODEL.meta.product }),
        el("div", { class: "rail__surface", text: MODEL.meta.surface + " · wireframe" })));

    MODEL.meta.nav_groups.slice().sort(function (a, b) { return a.order - b.order; })
      .forEach(function (group) {
        var members = MODEL.screens.filter(function (s) { return s.nav.group === group.id; });
        if (!members.length) return;
        nav.appendChild(el("div", { class: "rail__group" },
          el("div", { class: "rail__grouplabel", text: group.label }),
          group.hint ? el("div", { class: "rail__grouphint", text: group.hint }) : null,
          el("ul", { class: "rail__list" }, members.map(function (s) {
            return el("li", {}, el("button", {
              class: "rail__link",
              "aria-current": state.view === s.id ? "true" : "false",
              onclick: function () { go(s.id); }
            }, s.title));
          }))));
      });

    nav.appendChild(el("div", { class: "rail__group" },
      el("div", { class: "rail__grouplabel", text: "Reference" }),
      el("div", { class: "rail__grouphint", text: "The system this wireframe is generated from" }),
      el("ul", { class: "rail__list" },
        el("li", {}, el("button", {
          class: "rail__link", "aria-current": state.view === "ref:primitives" ? "true" : "false",
          onclick: function () { go("ref:primitives"); }
        }, "Vocabulary")),
        el("li", {}, el("button", {
          class: "rail__link", "aria-current": state.view === "ref:entities" ? "true" : "false",
          onclick: function () { go("ref:entities"); }
        }, "Model")))));

    nav.appendChild(el("div", { class: "rail__controls" },
      el("button", {
        class: "toggle", "aria-pressed": String(state.annotations),
        onclick: function () { state.annotations = !state.annotations; render(); }
      }, el("span", { class: "toggle__box" }), "Annotations"),
      el("button", {
        class: "toggle", "aria-pressed": String(document.documentElement.dataset.theme === "dark"),
        onclick: function () {
          var root = document.documentElement;
          root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
          render();
        }
      }, el("span", { class: "toggle__box" }), "Dark"),
      el("div", { class: "rail__grouphint", style: "margin-top:6px",
        text: MODEL.stats.screens + " screens · " + MODEL.stats.nodes + " nodes · built " + MODEL.meta.generated })));

    return nav;
  }

  function go(view) {
    state.view = view;
    if (history.replaceState) history.replaceState(null, "", "#" + view);
    render();
    window.scrollTo(0, 0);
  }

  function body() {
    if (state.view === "ref:primitives") return primitivesView();
    if (state.view === "ref:entities") return entitiesView();
    var screen = MODEL.screens.filter(function (s) { return s.id === state.view; })[0];
    return screen ? screenView(screen) : primitivesView();
  }

  function render() {
    document.documentElement.setAttribute("data-annotations", state.annotations ? "on" : "off");
    var app = document.getElementById("app");
    app.textContent = "";
    app.appendChild(el("div", { class: "shell" }, rail(), el("main", { class: "main" }, body())));
  }

  /* -------------------------------------------------------------- boot
   *
   * Two ways in, and the normal one is the fetch.
   *
   * Served locally, the model is a separate file the page pulls at load. That is what
   * makes the edit loop short: change a spec, re-run build.py, refresh — the HTML, the
   * CSS and this file are untouched, so there is nothing to regenerate and nothing to
   * cache-bust. Editing the CSS or this file needs no build at all.
   *
   * The inline path exists for the single-file `--standalone` build, which has to work
   * with no server at all.
   */
  function boot(model) {
    MODEL = model;
    state.view = MODEL.screens.length ? MODEL.screens[0].id : "ref:primitives";
    var hash = window.location.hash.slice(1);
    if (hash) state.view = decodeURIComponent(hash);
    render();
  }

  function fail(heading, detail) {
    document.getElementById("app").appendChild(
      el("div", { class: "main" },
        el("div", { class: "titleblock" },
          el("div", { class: "titleblock__body" },
            el("h1", { class: "titleblock__name", text: heading }),
            el("p", { class: "titleblock__purpose", text: detail })))));
  }

  var inline = document.getElementById("model");
  if (inline) {
    boot(JSON.parse(inline.textContent));
  } else if (window.location.protocol === "file:") {
    // fetch() is blocked on file:// by every browser, so say the useful thing rather
    // than leaving a blank page and a console error.
    fail("Serve this over HTTP",
      "This page loads wireframe.json at runtime, which a browser will not do from the "
      + "filesystem. Run  python3 -m http.server 8000 --directory web/wireframe  and open "
      + "http://localhost:8000. For a version that opens straight from disk, build with "
      + "python3 build.py --standalone.");
  } else {
    fetch("wireframe.json", { cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(boot)
      .catch(function (e) {
        fail("No model to draw",
          "Could not load wireframe.json (" + e.message + "). Run  python3 build.py  "
          + "in web/wireframe to generate it.");
      });
  }
})();
