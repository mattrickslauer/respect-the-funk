#!/usr/bin/env python3
"""Resolve the wireframe specs into one self-contained page.

    python3 web/wireframe/build.py

Standard library only, on purpose: the wireframe must build on any machine that can
run Python, without the application's dependencies installed. That is why the domain
model is read with `ast` rather than imported — the field lists below come from the
real `models.py`, but nothing here needs pydantic, and nothing here can be broken by
an import-time side effect in the app.

Three things happen, in order:

1. **Introspect.** `app/remixkit/domain/models.py` is parsed into an entity registry:
   every class, its fields, their annotations, and whether they carry a default.
   Base-class fields are folded into subclasses, so `Artist` knows about `tenant_id`.

2. **Validate.** Every node in every screen spec must name a primitive declared in
   `spec/primitives.json`, may carry only the props that primitive declares, and — if
   it binds an entity — must name an entity that exists and fields that exist on it.
   A violation stops the build. That is the whole value of keeping the vocabulary as
   data: a typo becomes an error instead of a block that silently renders as nothing.

3. **Emit.** `wireframe.json` (the resolved model) and `index.html` (that model, the
   renderer, and the stylesheet, inlined into one file that opens from disk).

Adding a screen is a JSON file. Adding a field is an edit to the domain model. Neither
is a change to this script, and neither is a change to the renderer.
"""

from __future__ import annotations

import ast
import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
MODELS = REPO / "app" / "remixkit" / "domain" / "models.py"
SPEC = HERE / "spec"
SRC = HERE / "src"
OUT_JSON = HERE / "wireframe.json"
# Only written with --standalone. index.html is hand-written and served, not generated.
OUT_STANDALONE = HERE / "standalone.html"


class BuildError(Exception):
    """A spec that cannot be trusted to render. Always fatal."""


# ---------------------------------------------------------------- 1. introspect
# Coarse kinds, not Python types. The renderer needs to know what *shape* of control
# a field implies, and nothing finer than this survives contact with a wireframe.
_KINDS = [
    ("bool", "bool"),
    ("datetime", "date"),
    ("float", "number"),
    ("int", "number"),
    ("dict", "map"),
    ("list", "list"),
    ("str", "text"),
]


def _kind_of(annotation: str, enums: set[str], objects: set[str]) -> str:
    base = annotation.replace(" ", "")
    # Longest name first so `HookWindow` is not shadowed by a shorter substring match,
    # then alphabetically — a set's iteration order is not stable across processes, and
    # letting it decide here would let the same annotation resolve to a different kind
    # on different runs.
    for name in sorted(enums | objects, key=lambda n: (-len(n), n)):
        if name in base:
            return "enum" if name in enums else "object"
    if base.startswith("list[") or base.startswith("list["):
        return "list"
    for needle, kind in _KINDS:
        if needle in base:
            return kind
    return "text"


def introspect(path: Path) -> dict:
    if not path.exists():
        raise BuildError(f"domain model not found at {path}")
    tree = ast.parse(path.read_text())

    classes: dict[str, ast.ClassDef] = {
        n.name: n for n in tree.body if isinstance(n, ast.ClassDef)
    }
    enums = {
        name for name, node in classes.items()
        if any(isinstance(b, ast.Name) and b.id == "Enum" for b in node.bases)
        or any(isinstance(b, ast.Attribute) and b.attr == "Enum" for b in node.bases)
    }
    objects = set(classes) - enums

    def own_fields(node: ast.ClassDef) -> list[dict]:
        out = []
        for stmt in node.body:
            if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
                continue
            annotation = ast.unparse(stmt.annotation)
            optional = "None" in annotation or stmt.value is not None
            out.append({
                "name": stmt.target.id,
                "annotation": annotation,
                "kind": _kind_of(annotation, enums, objects),
                "required": not optional,
            })
        return out

    def resolve(name: str, seen: frozenset[str] = frozenset()) -> list[dict]:
        """Fields of a class, with its in-module bases folded in first."""
        if name in seen:
            raise BuildError(f"circular inheritance at {name}")
        node = classes[name]
        inherited: list[dict] = []
        for base in node.bases:
            base_name = base.id if isinstance(base, ast.Name) else None
            if base_name in classes and base_name not in enums:
                for field in resolve(base_name, seen | {name}):
                    inherited.append({**field, "inherited": True})
        taken = {f["name"] for f in inherited}
        return inherited + [f for f in own_fields(node) if f["name"] not in taken]

    # Sorted, because `wireframe.json` is committed. Iterating the sets directly makes
    # the output depend on string hash randomisation, so every build rewrote the file
    # with the same entities in a different order — a few hundred lines of diff saying
    # nothing. A generated file that is checked in has to be reproducible or it cannot
    # be reviewed.
    registry = {}
    for name in sorted(objects):
        fields = resolve(name)
        if fields:
            registry[name] = {"name": name, "fields": fields}

    for name in sorted(enums):
        values = [
            ast.literal_eval(s.value)
            for s in classes[name].body
            if isinstance(s, ast.Assign) and isinstance(s.value, ast.Constant)
        ]
        registry[name] = {"name": name, "enum": True, "values": values, "fields": []}

    return registry


# ---------------------------------------------------------------- 2. validate
def resolve_fields(node: dict, entities: dict, where: str) -> dict | None:
    """Turn `entity` + `fields` into a concrete column/input list for the renderer."""
    entity = node.get("entity") or node.get("of")
    if not entity:
        return None
    if entity not in entities:
        raise BuildError(
            f"{where}: unknown entity {entity!r}. "
            f"Known: {', '.join(sorted(entities))}"
        )
    available = entities[entity]["fields"]
    by_name = {f["name"]: f for f in available}

    selector = node.get("fields", "auto")
    if selector == "auto":
        chosen = list(available)
    elif selector == "own":
        chosen = [f for f in available if not f.get("inherited")]
    elif isinstance(selector, list):
        missing = [f for f in selector if f not in by_name]
        if missing:
            raise BuildError(
                f"{where}: {entity} has no field(s) {', '.join(missing)}. "
                f"Available: {', '.join(by_name)}"
            )
        chosen = [by_name[f] for f in selector]
    else:
        raise BuildError(f"{where}: `fields` must be 'auto', 'own', or a list")

    for name in node.get("exclude", []) or []:
        if name not in by_name:
            raise BuildError(f"{where}: cannot exclude unknown field {name!r} of {entity}")
        chosen = [f for f in chosen if f["name"] != name]

    return {"entity": entity, "fields": chosen}


ALWAYS_ALLOWED = {"type", "note", "children"}


def walk(node, primitives: dict, entities: dict, where: str) -> dict:
    if not isinstance(node, dict):
        raise BuildError(f"{where}: expected an object, got {type(node).__name__}")
    kind = node.get("type")
    if kind not in primitives:
        raise BuildError(
            f"{where}: unknown primitive {kind!r}. "
            f"Declared: {', '.join(sorted(primitives))}"
        )

    declared = set(primitives[kind]["props"]) | ALWAYS_ALLOWED
    stray = set(node) - declared
    if stray:
        raise BuildError(
            f"{where}: {kind} does not take {', '.join(sorted(stray))}. "
            f"It takes: {', '.join(sorted(declared - ALWAYS_ALLOWED))}"
        )

    resolved = dict(node)
    binding = resolve_fields(node, entities, where)
    if binding:
        resolved["binding"] = binding

    children = node.get("children")
    if children is not None:
        if not isinstance(children, list):
            raise BuildError(f"{where}: `children` must be a list")
        resolved["children"] = [
            walk(child, primitives, entities, f"{where} > {child.get('type', '?')}[{i}]")
            for i, child in enumerate(children)
        ]

    # Tabs nest node lists one level deeper than the generic `children` walk reaches.
    if kind == "tabs":
        resolved["items"] = [
            {**item, "children": [
                walk(c, primitives, entities, f"{where} > tab({item.get('label')})[{i}]")
                for i, c in enumerate(item.get("children", []))
            ]}
            for item in node.get("items", [])
        ]

    return resolved


# ---------------------------------------------------------------- 3. emit
def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise BuildError(f"{path.relative_to(REPO)}: invalid JSON — {exc}") from exc


def build() -> dict:
    entities = introspect(MODELS)
    meta = load(SPEC / "meta.json")
    primitives = load(SPEC / "primitives.json")["primitives"]

    groups = {g["id"]: g for g in meta["nav_groups"]}
    screens = []
    for path in sorted((SPEC / "screens").glob("*.json")):
        raw = load(path)
        for key in ("id", "title", "route", "nav", "body"):
            if key not in raw:
                raise BuildError(f"{path.name}: missing required key {key!r}")
        group = raw["nav"].get("group")
        if group not in groups:
            raise BuildError(
                f"{path.name}: nav group {group!r} is not declared in meta.json "
                f"({', '.join(groups)})"
            )
        raw["body"] = walk(raw["body"], primitives, entities, f"{raw['id']}.body")
        screens.append(raw)

    screens.sort(key=lambda s: (groups[s["nav"]["group"]]["order"], s["nav"].get("order", 0)))

    counts: dict[str, int] = {}

    def tally(node):
        counts[node["type"]] = counts.get(node["type"], 0) + 1
        for child in node.get("children", []) or []:
            tally(child)

    for screen in screens:
        tally(screen["body"])

    return {
        "meta": {**meta, "generated": date.today().isoformat()},
        "primitives": primitives,
        "entities": entities,
        "screens": screens,
        "stats": {
            "screens": len(screens),
            "entities": len([e for e in entities.values() if not e.get("enum")]),
            "nodes": sum(counts.values()),
            "by_type": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        },
    }


def emit(model: dict, standalone: bool = False) -> list[Path]:
    """Write the resolved model. The served page reads it at load and is never rewritten."""
    OUT_JSON.write_text(json.dumps(model, indent=2) + "\n")
    written = [OUT_JSON]

    if standalone:
        # One file, everything inlined, no server required. For sending to someone —
        # the local working copy is index.html + http.server.
        css = (SRC / "wireframe.css").read_text()
        js = (SRC / "renderer.js").read_text()
        meta = model["meta"]
        # `</script>` inside the payload would close the tag early; escaping the slash
        # is the standard fix and stays valid JSON.
        payload = json.dumps(model, separators=(",", ":")).replace("</", "<\\/")

        OUT_STANDALONE.write_text(f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{meta['product']} — {meta['surface']} wireframe</title>
<style>
{css}
</style>
</head>
<body>
<div id="app"></div>
<script id="model" type="application/json">{payload}</script>
<script>
{js}
</script>
</body>
</html>
""")
        written.append(OUT_STANDALONE)

    return written


def main(argv: list[str]) -> int:
    standalone = "--standalone" in argv
    unknown = [a for a in argv if a not in ("--standalone",)]
    if unknown:
        print(f"wireframe: unknown option(s) {' '.join(unknown)}\n"
              f"usage: build.py [--standalone]", file=sys.stderr)
        return 2

    try:
        model = build()
    except BuildError as exc:
        print(f"wireframe: {exc}", file=sys.stderr)
        return 1

    written = emit(model, standalone=standalone)
    stats = model["stats"]
    print(f"wireframe: {stats['screens']} screens, {stats['nodes']} nodes, "
          f"{stats['entities']} entities from {MODELS.relative_to(REPO)}")
    for kind, n in list(stats["by_type"].items())[:6]:
        print(f"  {kind:<10} {n}")
    for path in written:
        print(f"  → {path.relative_to(REPO)}")
    if not standalone:
        print("\nserve it:  python3 -m http.server 8000 --directory "
              f"{HERE.relative_to(REPO)}\n           http://localhost:8000")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
