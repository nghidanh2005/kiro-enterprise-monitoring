"""Diagram helper for AWS draw.io files: AWS4 icons, groups, notes, numbered edges,
and a geometry check (overlaps, frame overflow, edges crossing icons or labels).
Copied byte-equal from an earlier internal diagram generator (Diagram class).
"""
import os
import sys
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))
ICON = 60
COLORS = {"analytics": "#8C4FFF", "compute": "#ED7100", "storage": "#7AA116",
          "mgmt": "#E7157B", "integ": "#E7157B", "security": "#DD344C", "ml": "#01A88D"}
NOTE = {"orange": ("#FFF4E5", "#ED7100", "#232F3E"), "red": ("#FDECEC", "#D13212", "#B0281A"),
        "grey": ("#F2F3F3", "#879596", "#232F3E")}
PTS = ("points=[[0,0,0],[0.25,0,0],[0.5,0,0],[0.75,0,0],[1,0,0],[0,1,0],[0.25,1,0],[0.5,1,0],"
       "[0.75,1,0],[1,1,0],[0,0.25,0],[0,0.5,0],[0,0.75,0],[1,0.25,0],[1,0.5,0],[1,0.75,0]];")
LBL = {"below": "verticalLabelPosition=bottom;verticalAlign=top;align=center;",
       "right": "labelPosition=right;verticalLabelPosition=middle;align=left;verticalAlign=middle;spacingLeft=2;",
       "top": "verticalLabelPosition=top;verticalAlign=bottom;align=center;"}


class Diagram:
    def __init__(self, title, width, height):
        self.title, self.w, self.h = title, width, height
        self.cells, self.boxes, self.edges, self.groups = [], [], [], {}
        self.n = 0

    def _id(self, p):
        self.n += 1
        return f"{p}{self.n}"

    # ---- containers -------------------------------------------------------
    def group(self, key, label, x, y, w, h, kind, parent=None):
        gid = self._id("g")
        px, py = (self.groups[parent][1], self.groups[parent][2]) if parent else (0, 0)
        icon = {"account": "group_account;strokeColor=#CD2264;fontColor=#CD2264;dashed=0;",
                "region": "group_region;strokeColor=#00A4A6;fontColor=#147EBA;dashed=1;",
                "sfn": "group_aws_step_functions_workflow;strokeColor=#CD2264;fontColor=#CD2264;dashed=0;"}[kind]
        style = (PTS + "outlineConnect=0;gradientColor=none;html=1;whiteSpace=wrap;fontSize=13;fontStyle=1;"
                 "container=1;pointerEvents=0;collapsible=0;recursiveResize=0;shape=mxgraph.aws4.group;"
                 f"fillColor=none;verticalAlign=top;align=left;spacingLeft=30;grIcon=mxgraph.aws4.{icon}")
        self.cells.append((gid, label, style, x - px, y - py, w, h, self.groups[parent][0] if parent else "1"))
        self.groups[key] = (gid, x, y, w, h)

    def _rel(self, parent, x, y):
        g = self.groups[parent]
        return g[0], x - g[1], y - g[2]

    # ---- nodes ------------------------------------------------------------
    def icon(self, key, parent, cx, cy, res, cat, lines, pos="below", shape="resourceIcon", faded=False):
        x, y = cx - ICON // 2, cy - ICON // 2
        pid, rx, ry = self._rel(parent, x, y)
        if shape == "user":
            style = ("sketch=0;outlineConnect=0;fontColor=#232F3E;gradientColor=none;fillColor=#232F3D;"
                     "strokeColor=none;dashed=0;html=1;fontSize=11;aspect=fixed;pointerEvents=1;shape=mxgraph.aws4.user;")
        else:
            style = (PTS + f"outlineConnect=0;fontColor=#232F3E;fillColor={COLORS[cat]};strokeColor=#ffffff;dashed=0;"
                     f"html=1;fontSize=11;fontStyle=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{res};")
        if faded:
            style += "opacity=40;textOpacity=60;"
        style += LBL[pos]
        cid = self._id("n")
        self.cells.append((cid, "<br>".join(escape(s) for s in lines), style, rx, ry, ICON, ICON, pid))
        self.boxes.append((key, "icon", x, y, ICON, ICON, parent))
        lw = max(len(s) for s in lines) * 6.5
        lh = len(lines) * 14
        if pos == "below":
            lb = (cx - lw / 2, y + ICON + 2, lw, lh)
        elif pos == "top":
            lb = (cx - lw / 2, y - 2 - lh, lw, lh)
        else:
            lb = (x + ICON + 2, cy - lh / 2, lw, lh)
        self.boxes.append((key + ":label", "label", *lb, parent))
        self.groups.setdefault("_nodes", {})[key] = (cid, x, y)

    def note(self, key, parent, x, y, w, h, color, text):
        fill, stroke, font = NOTE[color]
        pid, rx, ry = self._rel(parent, x, y)
        style = (f"rounded=1;arcSize=6;whiteSpace=wrap;html=1;fontSize=11;align=left;verticalAlign=top;spacing=6;"
                 f"fillColor={fill};strokeColor={stroke};fontColor={font};")
        self.cells.append((self._id("t"), escape(text), style, rx, ry, w, h, pid))
        self.boxes.append((key, "note", x, y, w, h, parent))

    def text(self, key, parent, cx, cy, lines, size=10, bold=False, align="center"):
        lw = max(len(s) for s in lines) * (6.0 if size <= 10 else 9.5)
        lh = len(lines) * (13 if size <= 10 else 22)
        x = cx - lw / 2 if align == "center" else cx
        y = cy - lh / 2
        pid, rx, ry = self._rel(parent, x, y) if parent else ("1", x, y)
        style = (f"text;html=1;align={align};verticalAlign=middle;fontSize={size};fontColor=#545B64;"
                 f"fontStyle={1 if bold else 0};whiteSpace=nowrap;")
        if bold:
            style = style.replace("#545B64", "#232F3E")
        self.cells.append((self._id("x"), "<br>".join(escape(s) for s in lines), style, rx, ry, lw, lh, pid))
        self.boxes.append((key, "text", x, y, lw, lh, parent))

    def edge(self, src, tgt, exit_, entry, points=(), dashed=False, num=None):
        self.edges.append((src, tgt, exit_, entry, list(points), dashed, num))
        if num is None:
            return
        nodes = self.groups["_nodes"]
        path = [(nodes[src][1] + exit_[0] * ICON, nodes[src][2] + exit_[1] * ICON), *points,
                (nodes[tgt][1] + entry[0] * ICON, nodes[tgt][2] + entry[1] * ICON)]
        seg = max(zip(path, path[1:]), key=lambda p: abs(p[1][0] - p[0][0]) + abs(p[1][1] - p[0][1]))
        cx, cy = (seg[0][0] + seg[1][0]) / 2, (seg[0][1] + seg[1][1]) / 2
        b = 24
        style = ("ellipse;whiteSpace=wrap;html=1;aspect=fixed;fillColor=#232F3E;strokeColor=#FFFFFF;"
                 "strokeWidth=2;fontColor=#FFFFFF;fontStyle=1;fontSize=11;")
        self.cells.append((self._id("b"), str(num), style, cx - b / 2, cy - b / 2, b, b, "1"))
        self.boxes.append((f"badge{num}", "badge", cx - b / 2, cy - b / 2, b, b, None))

    # ---- output -----------------------------------------------------------
    def xml(self):
        return '<mxfile host="app.diagrams.net" type="device">\n' + self.page("d1") + "\n</mxfile>"

    def page(self, did):
        out = [f'<diagram name="{escape(self.title)}" id="{did}">',
               f'<mxGraphModel dx="{self.w}" dy="{self.h}" grid="1" gridSize="10" guides="1" tooltips="1" '
               f'connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="{self.w}" '
               f'pageHeight="{self.h}" math="0" shadow="0"><root>',
               '<mxCell id="0"/><mxCell id="1" parent="0"/>']
        for cid, val, style, x, y, w, h, parent in self.cells:
            out.append(f'<mxCell id="{cid}" value="{escape(val, {chr(34): "&quot;"})}" style="{style}" vertex="1" '
                       f'parent="{parent}"><mxGeometry x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" '
                       'as="geometry"/></mxCell>')
        nodes = self.groups["_nodes"]
        for i, (s, t, ex, en, pts, dashed, _num) in enumerate(self.edges):
            style = ("edgeStyle=none;html=1;rounded=0;endArrow=block;endFill=1;strokeColor=#545B64;strokeWidth=1.5;"
                     f"exitX={ex[0]};exitY={ex[1]};exitDx=0;exitDy=0;entryX={en[0]};entryY={en[1]};entryDx=0;entryDy=0;")
            if dashed:
                style += "dashed=1;strokeColor=#879596;"
            pxml = "".join(f'<mxPoint x="{px}" y="{py}"/>' for px, py in pts)
            out.append(f'<mxCell id="e{i}" style="{style}" edge="1" parent="1" source="{nodes[s][0]}" '
                       f'target="{nodes[t][0]}"><mxGeometry relative="1" as="geometry">'
                       f'{"<Array as=" + chr(34) + "points" + chr(34) + ">" + pxml + "</Array>" if pts else ""}'
                       '</mxGeometry></mxCell>')
        out.append("</root></mxGraphModel></diagram>")
        return "\n".join(out)

    # ---- geometry check ---------------------------------------------------
    def check(self):
        problems = []
        ov = lambda a, b: a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]
        boxes = [(k, kind, (x, y, w, h), p) for k, kind, x, y, w, h, p in self.boxes]
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                if a[0].split(":")[0] == b[0].split(":")[0]:
                    continue
                if ov(a[2], b[2]):
                    problems.append(f"OVERLAP {a[0]} x {b[0]}")
        for k, kind, (x, y, w, h), p in boxes:
            g = self.groups[p] if p else (None, 0, -28, self.w, self.h + 28)
            if x < g[1] + 2 or y < g[2] + 28 or x + w > g[1] + g[3] - 2 or y + h > g[2] + g[4] - 2:
                problems.append(f"OUTSIDE {k} of {p}")
        nodes = self.groups["_nodes"]
        def seg_hits(p1, p2, r):
            x0, y0, w, h = r
            (ax, ay), (bx, by) = p1, p2
            if ax == bx:
                return x0 < ax < x0 + w and max(min(ay, by), y0) < min(max(ay, by), y0 + h)
            if ay == by:
                return y0 < ay < y0 + h and max(min(ax, bx), x0) < min(max(ax, bx), x0 + w)
            return True  # diagonal segments are not allowed in these diagrams
        for s, t, ex, en, pts, _, num in self.edges:
            sx, sy = nodes[s][1] + ex[0] * ICON, nodes[s][2] + ex[1] * ICON
            tx, ty = nodes[t][1] + en[0] * ICON, nodes[t][2] + en[1] * ICON
            path = [(sx, sy), *pts, (tx, ty)]
            for p1, p2 in zip(path, path[1:]):
                for k, kind, r, _ in boxes:
                    if k in (s, t) or k == f"badge{num}":
                        continue
                    if seg_hits(p1, p2, r):
                        problems.append(f"EDGE {s}->{t} crosses {k}")
        return problems
