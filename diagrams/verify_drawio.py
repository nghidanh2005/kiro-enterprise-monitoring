"""Verify a .drawio file independently of the generator that wrote it.

Checks per page: XML parses; every edge's source and target exist; every mxgraph.aws4 name used
(resIcon, grIcon, shape) exists in draw.io's Sidebar-AWS4.js; every note's wrapped text fits its box;
every vertex lies inside the page.
Usage: python verify_drawio.py <file.drawio> <Sidebar-AWS4.js>
"""
import html
import math
import re
import sys
import xml.etree.ElementTree as ET

path, sidebar = sys.argv[1], sys.argv[2]
lib = open(sidebar, encoding="utf-8", errors="replace").read()


def in_library(kind, name):
    if kind == "resIcon":
        return f"resIcon=' + gn + '.{name};" in lib
    if kind == "grIcon":
        return f"grIcon=' + gn + '.{name};" in lib
    return f"{name};" in lib  # shape=mxgraph.aws4.<name>; is built as prefix + '<name>;'


def wrapped_lines(text, width_px, char_px=6.2):
    per_line = max(1, int((width_px - 12) // char_px))
    total = 0
    for para in text.split("\n"):
        words, cur = para.split(), 0
        if not words:
            total += 1
            continue
        lines = 1
        for w in words:
            need = len(w) if cur == 0 else cur + 1 + len(w)
            if need <= per_line:
                cur = need
            else:
                lines += math.ceil(len(w) / per_line) if len(w) > per_line else 1
                cur = len(w) % per_line if len(w) > per_line else len(w)
        total += lines
    return total


root = ET.parse(path).getroot()
pages = root.findall("diagram")
print("pages:", len(pages), [p.get("name") for p in pages])
grand = {"dangling": 0, "missing": set(), "overflow_notes": 0, "outside": 0, "vertices": 0, "edges": 0}
for p in pages:
    model = p.find("mxGraphModel")
    pw, ph = float(model.get("pageWidth")), float(model.get("pageHeight"))
    cells = {c.get("id"): c for c in p.iter("mxCell")}

    def absxy(c):
        g = c.find("mxGeometry")
        x, y = float(g.get("x", 0)), float(g.get("y", 0))
        par = cells.get(c.get("parent"))
        if par is not None and par.get("vertex") == "1":
            px, py = absxy(par)
            x, y = x + px, y + py
        return x, y

    names, dangling, notes_bad, outside, nv, ne = set(), 0, [], [], 0, 0
    for c in cells.values():
        style = c.get("style") or ""
        names.update(re.findall(r"(resIcon|grIcon|shape)=mxgraph\.aws4\.([A-Za-z0-9_]+)", style))
        if c.get("edge") == "1":
            ne += 1
            if c.get("source") not in cells or c.get("target") not in cells:
                dangling += 1
            continue
        if c.get("vertex") != "1":
            continue
        nv += 1
        g = c.find("mxGeometry")
        tags = {t.lower() for t in re.findall(r"</?\s*([A-Za-z][^\s>/]*)", c.get("value") or "")}
        if tags - {"br", "font", "div", "b", "i", "span"}:
            notes_bad.append(f"{c.get('id')} label has unknown HTML tag(s) {sorted(tags)}, text will vanish")
        x, y = absxy(c)
        w, h = float(g.get("width")), float(g.get("height"))
        if x < 0 or y < 0 or x + w > pw or y + h > ph:
            outside.append(c.get("id"))
        if "arcSize=6" in style:  # notes
            text = html.unescape(re.sub(r"<br\s*/?>", "\n", c.get("value") or ""))
            need = wrapped_lines(text, w) * 14 + 12
            if need > h:
                notes_bad.append(f"{c.get('id')} needs {need}px > {h:.0f}px")
    missing = sorted(f"{k}:{n}" for k, n in names if not in_library(k, n))

    # edge polylines in absolute coordinates, then count perpendicular crossings between different edges
    def endpoint(cell_id, fx, fy):
        c = cells[cell_id]
        g = c.find("mxGeometry")
        x, y = absxy(c)
        return x + fx * float(g.get("width")), y + fy * float(g.get("height"))

    polys = []
    for c in cells.values():
        if c.get("edge") != "1" or c.get("source") not in cells or c.get("target") not in cells:
            continue
        st = dict(kv.split("=", 1) for kv in (c.get("style") or "").split(";") if "=" in kv)
        pts = [(float(m.get("x")), float(m.get("y"))) for m in c.iter("mxPoint")]
        polys.append([endpoint(c.get("source"), float(st.get("exitX", 0.5)), float(st.get("exitY", 0.5))), *pts,
                      endpoint(c.get("target"), float(st.get("entryX", 0.5)), float(st.get("entryY", 0.5)))])

    def cross(a, b):
        (ax1, ay1), (ax2, ay2) = a
        (bx1, by1), (bx2, by2) = b
        if ax1 == ax2 and by1 == by2:  # a vertical, b horizontal
            return min(bx1, bx2) < ax1 < max(bx1, bx2) and min(ay1, ay2) < by1 < max(ay1, ay2)
        if ay1 == ay2 and bx1 == bx2:
            return cross(b, a)
        return False

    crossings = 0
    for i in range(len(polys)):
        for j in range(i + 1, len(polys)):
            for a in zip(polys[i], polys[i][1:]):
                for b in zip(polys[j], polys[j][1:]):
                    crossings += bool(cross(a, b))
    print(f"[{p.get('name')}] page {pw:.0f}x{ph:.0f}: vertices={nv} edges={ne} dangling={dangling} "
          f"aws4_names={len(names)} missing={missing} notes_overflow={notes_bad} outside_page={outside} "
          f"edge_crossings={crossings}")
    grand["dangling"] += dangling
    grand["missing"].update(missing)
    grand["overflow_notes"] += len(notes_bad)
    grand["outside"] += len(outside)
    grand["vertices"] += nv
    grand["edges"] += ne
    print("   names:", ", ".join(sorted(f"{k}:{n}" for k, n in names)))
ok = not (grand["dangling"] or grand["missing"] or grand["overflow_notes"] or grand["outside"])
print("TOTAL", {k: (sorted(v) if isinstance(v, set) else v) for k, v in grand.items()}, "OK" if ok else "FAIL")
sys.exit(0 if ok else 1)
