"""Render a topology Graph to standalone SVG (used by `export` and the web UI)."""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from . import security
from .topology import Graph, PALETTE, STATUS_COLORS

BG = "#0b1220"
PANEL = "#111c31"
STROKE = "#25344f"
TEXT = "#e2e8f0"
MUTED = "#8b9bb4"

EDGE_STYLE: Dict[str, Dict[str, str]] = {
    "owns": {"color": "#3f5477", "dash": "", "width": "1.8"},
    "selects": {"color": "#22d3ee", "dash": "6 4", "width": "1.6"},
    "selects-notready": {"color": "#64748b", "dash": "3 5", "width": "1.4"},
    "routes": {"color": "#f59e0b", "dash": "", "width": "2"},
    "mounts": {"color": "#fbbf24", "dash": "2 4", "width": "1.4"},
    "scales": {"color": "#c084fc", "dash": "5 3", "width": "1.6"},
    "binds": {"color": "#2dd4bf", "dash": "4 3", "width": "1.6"},
}


def _esc(text) -> str:
    """Escape text for both element content and attribute values.

    Object names reach here straight from applied manifests, and the SVG we
    produce is opened in a browser, so an unescaped `<` is a script-injection
    hole in a file the user may well share (CWE-79). Both quote characters are
    escaped so the same function is safe in single- and double-quoted
    attributes, and control characters are dropped because they are not legal
    XML and make some viewers reject the whole document.
    """
    cleaned = "".join(ch for ch in str(text)
                      if ch in "\t\n\r" or ord(ch) >= 32)
    return (cleaned.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def _clip(text: str, limit: int) -> str:
    text = str(text)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _edge_path(src, dst) -> str:
    x1, y1 = src["x"] + src["w"] / 2, src["y"] + src["h"]
    x2, y2 = dst["x"] + dst["w"] / 2, dst["y"]
    if abs(src["y"] - dst["y"]) < 10:              # same row -> side to side
        x1, y1 = src["x"] + src["w"], src["y"] + src["h"] / 2
        x2, y2 = dst["x"], dst["y"] + dst["h"] / 2
        mid = (x1 + x2) / 2
        return f"M{x1:.1f},{y1:.1f} C{mid:.1f},{y1:.1f} {mid:.1f},{y2:.1f} {x2:.1f},{y2:.1f}"
    if y2 < y1:                                     # upward edge -> leave from the top
        x1, y1 = src["x"] + src["w"] / 2, src["y"]
        x2, y2 = dst["x"] + dst["w"] / 2, dst["y"] + dst["h"]
    mid = (y1 + y2) / 2
    return f"M{x1:.1f},{y1:.1f} C{x1:.1f},{mid:.1f} {x2:.1f},{mid:.1f} {x2:.1f},{y2:.1f}"


def to_svg(graph: Graph, title: str = "cluster topology",
           with_background: bool = True) -> str:
    data = graph.as_dict() if isinstance(graph, Graph) else graph
    nodes: List[dict] = data["nodes"]
    by_id = {n["id"]: n for n in nodes}
    width = max(float(data["width"]), 640) + 20
    height = max(float(data["height"]), 400) + 60

    out: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" '
        f'height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'font-family="Inter, Segoe UI, Helvetica, Arial, sans-serif">',
        "<defs>",
        '<filter id="glow" x="-40%" y="-40%" width="180%" height="180%">'
        '<feGaussianBlur stdDeviation="4" result="b"/>'
        '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>'
        "</filter>",
        '<linearGradient id="cardgrad" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="#16233c"/>'
        f'<stop offset="100%" stop-color="#101a2e"/></linearGradient>',
        "</defs>",
    ]
    if with_background:
        out.append(f'<rect width="100%" height="100%" fill="{BG}"/>')
        out.append(f'<text x="18" y="26" fill="{MUTED}" font-size="13">'
                   f"{_esc(title)}</text>")

    # groups: namespace lanes (logical view) and node boxes (physical view)
    for group in data.get("groups", []):
        colour = STATUS_COLORS.get(group.get("status", "ok"), MUTED)
        x, y, w, h = group["x"], group["y"] + 34, group["w"], group["h"]
        if group.get("kind") == "Namespace":
            out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" '
                       f'fill="#0d182b" stroke="#22314d" stroke-width="1.2" '
                       f'stroke-dasharray="7 5"/>')
            out.append(f'<rect x="{x}" y="{y}" width="{w}" height="26" rx="13" '
                       f'fill="#16233c" opacity="0.9"/>')
            out.append(f'<circle cx="{x + 16}" cy="{y + 13}" r="3.5" fill="{colour}"/>')
            out.append(f'<text x="{x + 28}" y="{y + 18}" fill="#cbd5e1" '
                       f'font-size="11.5" font-weight="600">'
                       f'{_esc(group["title"])}</text>')
            out.append(f'<text x="{x + w - 12}" y="{y + 18}" fill="{MUTED}" '
                       f'font-size="9.5" text-anchor="end">'
                       f'{_esc(group["subtitle"])}</text>')
            continue

        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" '
                   f'fill="{PANEL}" stroke="{colour}" stroke-width="1.4" '
                   f'opacity="0.95"/>')
        out.append(f'<text x="{x + 14}" y="{y + 24}" fill="{TEXT}" '
                   f'font-size="12.5" font-weight="600">{_esc(group["title"])}</text>')
        out.append(f'<text x="{x + 14}" y="{y + 40}" fill="{MUTED}" '
                   f'font-size="10">{_esc(group["subtitle"])}</text>')
        if group.get("kind") != "Node":
            continue
        bar_x = x + w - 96
        for index, key in enumerate(("cpu_pct", "mem_pct")):
            pct = max(0, min(100, int(group.get(key, 0))))
            bar_y = y + 18 + index * 14
            out.append(f'<rect x="{bar_x}" y="{bar_y}" width="80" height="7" rx="3.5" '
                       f'fill="#1e2b45"/>')
            fill = "#22c55e" if pct < 70 else ("#f59e0b" if pct < 90 else "#ef4444")
            out.append(f'<rect x="{bar_x}" y="{bar_y}" width="{80 * pct / 100:.1f}" '
                       f'height="7" rx="3.5" fill="{fill}"/>')
            out.append(f'<text x="{bar_x - 6}" y="{bar_y + 7}" fill="{MUTED}" '
                       f'font-size="9" text-anchor="end">{key[:3].upper()}</text>')

    # edges first so cards sit on top
    for edge in data["edges"]:
        src, dst = by_id.get(edge["src"]), by_id.get(edge["dst"])
        if not src or not dst:
            continue
        style = EDGE_STYLE.get(edge["kind"], EDGE_STYLE["owns"])
        dash = f' stroke-dasharray="{style["dash"]}"' if style["dash"] else ""
        colour = edge.get("colour") or style["color"]
        opacity = "0.5" if edge["kind"] == "selects-notready" else "0.8"
        out.append(f'<path d="{_edge_path(src, dst)}" fill="none" '
                   f'stroke="{colour}" stroke-width="{style["width"]}"'
                   f'{dash} opacity="{opacity}" class="edge {edge["kind"]}"/>')

    # cards
    for node in nodes:
        x, y, w, h = node["x"], node["y"] + (34 if data.get("groups") else 0), \
            node["w"], node["h"]
        colour = node["color"]
        status_colour = STATUS_COLORS.get(node["status"], MUTED)
        glow = ' filter="url(#glow)"' if node.get("fresh") else ""
        out.append(f'<g{glow}>')
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" '
                   f'fill="url(#cardgrad)" stroke="{colour}" stroke-width="1.5"/>')
        out.append(f'<rect x="{x}" y="{y}" width="5" height="{h}" rx="2.5" '
                   f'fill="{colour}"/>')
        out.append(f'<circle cx="{x + w - 11}" cy="{y + 11}" r="4" '
                   f'fill="{status_colour}"/>')
        chars = int((w - 34) / 6.2)
        out.append(f'<text x="{x + 11}" y="{y + 17}" fill="{TEXT}" font-size="10.5" '
                   f'font-weight="600">{_esc(_clip(node["label"], chars))}</text>')
        out.append(f'<text x="{x + 11}" y="{y + 29}" fill="{MUTED}" font-size="8.5">'
                   f'{_esc(node["kind"])}</text>')
        badge = _clip(node.get("badge") or "", 12)
        bw = 5.6 * len(badge) + 10 if badge else 0
        if node.get("sublabel"):
            room = int((w - 22 - bw) / 5.0)
            out.append(f'<text x="{x + 11}" y="{y + 41}" fill="{status_colour}" '
                       f'font-size="8.5">{_esc(_clip(node["sublabel"], room))}</text>')
        if badge:
            out.append(f'<rect x="{x + w - bw - 7:.1f}" y="{y + h - 17}" '
                       f'width="{bw:.1f}" height="13" rx="6.5" fill="#1b2942" '
                       f'stroke="{STROKE}"/>')
            out.append(f'<text x="{x + w - bw / 2 - 7:.1f}" y="{y + h - 7.5}" '
                       f'fill="{MUTED}" font-size="8" text-anchor="middle">'
                       f'{_esc(badge)}</text>')
        out.append("</g>")

    out.append("</svg>")
    return "\n".join(out)


def write_svg(graph: Graph, path: str, title: str = "cluster topology") -> str:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(to_svg(graph, title))
    return path


def to_dot(graph: Graph) -> str:
    """Graphviz export, handy if you want to re-render the topology elsewhere."""
    data = graph.as_dict() if isinstance(graph, Graph) else graph
    lines = ["digraph cluster {", '  rankdir=TB;', '  bgcolor="#0b1220";',
             '  node [shape=box style="rounded,filled" fontname="Helvetica" '
             'fontcolor="#e2e8f0" color="#25344f"];',
             '  edge [color="#3f5477"];']
    for node in data["nodes"]:
        lines.append(f'  "{node["id"]}" [label="{node["kind"]}\\n{node["name"]}" '
                     f'fillcolor="#16233c" color="{node["color"]}"];')
    for edge in data["edges"]:
        style = "dashed" if edge["kind"] != "owns" else "solid"
        lines.append(f'  "{edge["src"]}" -> "{edge["dst"]}" [style={style}];')
    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# interactive, self-contained HTML export
# ---------------------------------------------------------------------------
INTERACTIVE_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>__TITLE__</title>
<style>
:root{--bg:#0b1220;--panel:#111c31;--stroke:#25344f;--text:#e2e8f0;--muted:#8b9bb4;
      --accent:#38bdf8}
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:var(--bg);color:var(--text);overflow:hidden;
  font-family:Inter,'Segoe UI',system-ui,Helvetica,Arial,sans-serif}
#bar{display:flex;align-items:center;gap:8px;padding:9px 14px;background:var(--panel);
  border-bottom:1px solid var(--stroke);flex-wrap:wrap}
#bar b{font-size:13px}
#bar span.hint{color:var(--muted);font-size:11.5px}
button{font:inherit;font-size:12.5px;border:0;border-radius:8px;padding:6px 11px;
  background:#16233c;color:var(--text);cursor:pointer}
button:hover{background:#22344f}
button.primary{background:#0e7490;color:#fff}
#stage{position:absolute;inset:46px 0 0 0;overflow:hidden;cursor:grab;
  background:radial-gradient(circle at 20% 0%,#10203a 0,var(--bg) 60%)}
#stage.drag{cursor:grabbing}
#canvas{transform-origin:0 0}
svg .node{cursor:move}
svg .node.picked rect:first-of-type{stroke-width:3}
#tip{position:fixed;pointer-events:none;background:#0f1c31;border:1px solid var(--stroke);
  border-radius:8px;padding:6px 9px;font-size:11.5px;color:#cbd5e1;display:none;z-index:5}
</style></head><body>
<div id="bar">
  <b>__TITLE__</b>
  <span style="flex:1"></span>
  <button onclick="zoom(1/1.2)">&minus;</button>
  <button onclick="zoom(1.2)">+</button>
  <button onclick="fit()">Fit</button>
  <button onclick="actual()">100%</button>
  <button onclick="expand()" id="expandbtn">Full screen</button>
  <button class="primary" onclick="restore()">Restore layout</button>
  <button onclick="save()">Save .svg</button>
  <span class="hint">drag a card to move it &middot; drag the background to pan &middot;
    wheel to zoom</span>
</div>
<div id="stage"><div id="canvas">__SVG__</div></div>
<div id="tip"></div>
<script>
const stage=document.getElementById('stage'),canvas=document.getElementById('canvas'),
      svg=canvas.querySelector('svg'),tip=document.getElementById('tip');
const META=__META__;
let z=1,ox=0,oy=0,dragNode=null,dragStart=null,panning=false,panStart=null;
const home=new Map();

svg.querySelectorAll('g').forEach((g,i)=>{
  const meta=META[i]; if(!meta) return;
  g.classList.add('node'); g.dataset.i=i;
  home.set(g,{x:0,y:0}); g.dataset.dx=0; g.dataset.dy=0;
  g.addEventListener('mousedown',e=>{
    e.stopPropagation(); dragNode=g;
    dragStart={x:e.clientX,y:e.clientY,dx:+g.dataset.dx,dy:+g.dataset.dy};
    g.classList.add('picked');
  });
  g.addEventListener('mousemove',e=>{
    if(dragNode) return;
    tip.style.display='block'; tip.style.left=(e.clientX+14)+'px';
    tip.style.top=(e.clientY+14)+'px';
    tip.innerHTML='<b>'+meta.kind+'</b> '+meta.name+
      (meta.ns?'<br>namespace '+meta.ns:'')+(meta.detail?'<br>'+meta.detail:'')+
      (meta.sublabel?'<br>'+meta.sublabel:'');
  });
  g.addEventListener('mouseleave',()=>{tip.style.display='none';});
});

function apply(){canvas.style.transform='translate('+ox+'px,'+oy+'px) scale('+z+')';}
function zoom(f){z=Math.max(.15,Math.min(4,z*f));apply();}
function actual(){z=1;ox=0;oy=0;apply();}
function fit(){
  const w=+svg.getAttribute('width'),h=+svg.getAttribute('height');
  z=Math.min((stage.clientWidth-30)/w,(stage.clientHeight-30)/h);
  ox=(stage.clientWidth-w*z)/2;oy=14;apply();
}
function expand(){
  if(document.fullscreenElement){document.exitFullscreen();}
  else{document.documentElement.requestFullscreen();}
  setTimeout(fit,220);
}
function restore(){
  svg.querySelectorAll('g.node').forEach(g=>{
    g.dataset.dx=0;g.dataset.dy=0;g.removeAttribute('transform');
    g.classList.remove('picked');
  });
  fit();
}
function save(){
  const clone=svg.cloneNode(true);
  const blob=new Blob([clone.outerHTML],{type:'image/svg+xml'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);a.download='topology.svg';a.click();
}
window.addEventListener('mousemove',e=>{
  if(dragNode){
    const dx=dragStart.dx+(e.clientX-dragStart.x)/z,
          dy=dragStart.dy+(e.clientY-dragStart.y)/z;
    dragNode.dataset.dx=dx;dragNode.dataset.dy=dy;
    dragNode.setAttribute('transform','translate('+dx+','+dy+')');
  }else if(panning){
    ox=panStart.ox+(e.clientX-panStart.x);oy=panStart.oy+(e.clientY-panStart.y);apply();
  }
});
window.addEventListener('mouseup',()=>{
  if(dragNode) dragNode.classList.remove('picked');
  dragNode=null;panning=false;stage.classList.remove('drag');
});
stage.addEventListener('mousedown',e=>{
  panning=true;panStart={x:e.clientX,y:e.clientY,ox,oy};stage.classList.add('drag');
});
stage.addEventListener('wheel',e=>{e.preventDefault();zoom(e.deltaY<0?1.1:1/1.1);},
  {passive:false});
window.addEventListener('keydown',e=>{
  if(e.key==='0') actual(); if(e.key==='f') fit(); if(e.key==='r') restore();
});
fit();
</script></body></html>
"""


def to_interactive_html(graph: Graph, title: str = "cluster topology") -> str:
    """A standalone HTML file: zoom, pan, drag any object, restore, save.

    Two escaping rules matter here, because everything in the graph -- object
    names, images, namespaces -- ultimately came from a manifest the user
    applied, and the exported file gets opened in a browser and mailed around:

    * the metadata is embedded inside a ``<script>`` element, so any ``<`` in it
      is escaped to ``\\u003c``. Without that, an object named ``</script>...``
      closes the element and the rest is parsed as markup (CWE-79/CWE-116).
    * the substitutions are done in one pass, so a value inserted by an earlier
      substitution can never be re-read as a later placeholder.
    """
    data = graph.as_dict() if isinstance(graph, Graph) else graph
    svg = to_svg(graph, "", with_background=False)
    meta = [{"kind": n["kind"], "name": n["name"], "ns": n.get("ns", ""),
             "detail": n.get("detail", ""), "sublabel": n.get("sublabel", "")}
            for n in data["nodes"]]
    replacements = {
        "__TITLE__": _esc(title),
        "__META__": security.json_for_script(json.dumps(meta)),
        "__SVG__": svg,
    }
    return re.sub(r"__TITLE__|__META__|__SVG__",
                  lambda m: replacements[m.group(0)], INTERACTIVE_TEMPLATE)
