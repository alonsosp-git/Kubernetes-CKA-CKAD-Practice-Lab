"""Original, vector concept diagrams -- one per handbook topic.

These are drawn by this module as SVG, so they stay perfectly sharp at any zoom
level and carry no third-party artwork or watermark. They sit next to the
handbook's own page image in the UI: the diagram for reading on screen, the page
for the author's full treatment of the topic.

Each diagram is described by a small spec and rendered by one of five layouts:

    chain     a -> b -> c            pipelines and request paths
    tree      root over children     ownership (Deployment -> RS -> Pod)
    columns   titled columns         comparisons and option sets
    layers    stacked bands          control plane / node split, storage stack
    timeline  ordered phases         lifecycles and probe ordering
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

W = 980                      # design width; the SVG scales to any size

BG = "#0b1220"
CARD = "#16233c"
CARD2 = "#111c31"
STROKE = "#2b3d5c"
TEXT = "#e6edf7"
MUTED = "#93a4be"
LINE = "#3f5477"

C = {
    "blue": "#60a5fa", "cyan": "#22d3ee", "sky": "#38bdf8", "violet": "#a78bfa",
    "indigo": "#818cf8", "pink": "#f472b6", "amber": "#f59e0b", "yellow": "#fbbf24",
    "green": "#4ade80", "teal": "#2dd4bf", "red": "#f87171", "slate": "#94a3b8",
    "purple": "#c084fc", "rose": "#fb7185", "emerald": "#34d399",
}


def _esc(text: Any) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _wrap(text: str, width: int) -> List[str]:
    words, lines, current = str(text).split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


class Canvas:
    def __init__(self, width: int = W, height: int = 520):
        self.width, self.height = width, height
        self.parts: List[str] = []

    # -- primitives ----------------------------------------------------
    def rect(self, x, y, w, h, fill=CARD, stroke=STROKE, rx=12, width=1.4,
             dash="", opacity=1.0):
        dash = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{width}"{dash} '
            f'opacity="{opacity}"/>')

    def text(self, x, y, value, size=12, fill=TEXT, weight="400", anchor="start",
             family=""):
        family = f' font-family="{family}"' if family else ""
        self.parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
            f'font-weight="{weight}" text-anchor="{anchor}"{family}>'
            f"{_esc(value)}</text>")

    def line(self, x1, y1, x2, y2, stroke=LINE, width=1.6, dash="", arrow=True):
        dash = f' stroke-dasharray="{dash}"' if dash else ""
        marker = ' marker-end="url(#arrow)"' if arrow else ""
        self.parts.append(
            f'<path d="M{x1:.1f},{y1:.1f} L{x2:.1f},{y2:.1f}" fill="none" '
            f'stroke="{stroke}" stroke-width="{width}"{dash}{marker}/>')

    def curve(self, x1, y1, x2, y2, stroke=LINE, width=1.6, dash="", arrow=True):
        dash = f' stroke-dasharray="{dash}"' if dash else ""
        marker = ' marker-end="url(#arrow)"' if arrow else ""
        mid = (y1 + y2) / 2
        self.parts.append(
            f'<path d="M{x1:.1f},{y1:.1f} C{x1:.1f},{mid:.1f} {x2:.1f},{mid:.1f} '
            f'{x2:.1f},{y2:.1f}" fill="none" stroke="{stroke}" '
            f'stroke-width="{width}"{dash}{marker}/>')

    # -- composites ----------------------------------------------------
    def card(self, x, y, w, h, title, sub="", colour=C["sky"], body=None,
             tag="", muted=False):
        self.rect(x, y, w, h, fill=CARD2 if muted else CARD, stroke=colour,
                  width=1.6)
        self.parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="5" height="{h:.1f}" rx="2.5" '
            f'fill="{colour}"/>')
        self.text(x + 14, y + 21, title, size=13, weight="700")
        if sub:
            self.text(x + 14, y + 38, sub, size=10.5, fill=colour)
        offset = y + (54 if sub else 40)
        for line in (body or []):
            self.text(x + 14, offset, line, size=10.5, fill=MUTED)
            offset += 14
        if tag:
            tw = 6.4 * len(tag) + 14
            self.rect(x + w - tw - 10, y + 10, tw, 17, fill="#1b2942",
                      stroke=STROKE, rx=8.5, width=1)
            self.text(x + w - tw / 2 - 10, y + 22, tag, size=9.5, fill=MUTED,
                      anchor="middle")

    def chip(self, x, y, label, colour=C["slate"], w=None, h=26):
        w = w or (7.2 * len(str(label)) + 26)
        self.rect(x, y, w, h, fill="#152238", stroke=colour, rx=h / 2, width=1.3)
        self.text(x + w / 2, y + h / 2 + 4, label, size=11, fill=TEXT,
                  anchor="middle")
        return w

    def band(self, x, y, w, h, title, colour=C["slate"]):
        self.rect(x, y, w, h, fill="#0e1a2e", stroke=colour, rx=14, width=1.2,
                  dash="7 5", opacity=0.95)
        self.text(x + 16, y + 22, title, size=12, weight="700", fill=colour)

    def note(self, x, y, w, text, colour=C["amber"]):
        lines = _wrap(text, int(w / 6.1))
        height = 16 + 15 * len(lines)
        self.rect(x, y, w, height, fill="#141f33", stroke=colour, rx=9, width=1.2)
        for index, line in enumerate(lines):
            self.text(x + 12, y + 20 + index * 15, line, size=10.5, fill="#d7e2f2")
        return height

    # -- output --------------------------------------------------------
    def svg(self, title: str, subtitle: str = "") -> str:
        header = []
        if title:
            header.append(f'<text x="26" y="34" font-size="17" fill="{TEXT}" '
                          f'font-weight="700">{_esc(title)}</text>')
        if subtitle:
            header.append(f'<text x="26" y="55" font-size="11.5" fill="{MUTED}">'
                          f"{_esc(subtitle)}</text>")
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.width} '
            f'{self.height}" width="{self.width}" height="{self.height}" '
            'font-family="Inter, Segoe UI, Helvetica, Arial, sans-serif">'
            '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            f'<path d="M0,0 L10,5 L0,10 z" fill="{LINE}"/></marker></defs>'
            f'<rect width="100%" height="100%" fill="{BG}" rx="14"/>'
            + "".join(header) + "".join(self.parts) + "</svg>")


# ---------------------------------------------------------------------------
# layouts
# ---------------------------------------------------------------------------
def _layout_chain(spec: dict) -> str:
    steps = spec["steps"]
    notes = spec.get("notes", [])
    per_row = spec.get("per_row", 4)
    rows = [steps[i:i + per_row] for i in range(0, len(steps), per_row)]
    card_h = spec.get("card_h", 96)
    top = 84
    height = top + len(rows) * (card_h + 62) + (26 if notes else 0)
    canvas = Canvas(height=int(height + 20))

    positions = []
    for row_index, row in enumerate(rows):
        gap = 34
        card_w = (W - 52 - gap * (len(row) - 1)) / len(row)
        y = top + row_index * (card_h + 62)
        for index, step in enumerate(row):
            x = 26 + index * (card_w + gap)
            canvas.card(x, y, card_w, card_h, step["title"], step.get("sub", ""),
                        C.get(step.get("colour", "sky"), C["sky"]),
                        step.get("body", []), step.get("tag", ""))
            positions.append((x, y, card_w, card_h))
        for index in range(len(row) - 1):
            x = 26 + index * (card_w + gap)
            canvas.line(x + card_w + 5, y + card_h / 2, x + card_w + gap - 6,
                        y + card_h / 2)
            label = row[index].get("edge", "")
            if label:
                canvas.text(x + card_w + gap / 2, y + card_h / 2 - 8, label,
                            size=9.5, fill=MUTED, anchor="middle")
        if row_index < len(rows) - 1:
            canvas.line(W / 2, y + card_h + 8, W / 2, y + card_h + 48)

    y = top + len(rows) * (card_h + 62) - 34
    for text in notes:
        y += canvas.note(26, y, W - 52, text) + 10
    canvas.height = int(y + 24)
    return canvas.svg(spec["title"], spec.get("subtitle", ""))


def _layout_tree(spec: dict) -> str:
    root = spec["root"]
    children = spec["children"]
    grandchildren = spec.get("grandchildren", [])
    notes = spec.get("notes", [])
    card_h, root_w = 84, 300
    rows = 2 + (1 if grandchildren else 0)
    canvas = Canvas(height=110 + rows * (card_h + 56) + 60 * len(notes))

    top = 84
    canvas.card((W - root_w) / 2, top, root_w, card_h, root["title"],
                root.get("sub", ""), C.get(root.get("colour", "blue"), C["blue"]),
                root.get("body", []), root.get("tag", ""))

    y2 = top + card_h + 56
    gap = 26
    width2 = (W - 52 - gap * (len(children) - 1)) / max(1, len(children))
    for index, child in enumerate(children):
        x = 26 + index * (width2 + gap)
        canvas.card(x, y2, width2, card_h, child["title"], child.get("sub", ""),
                    C.get(child.get("colour", "indigo"), C["indigo"]),
                    child.get("body", []), child.get("tag", ""))
        canvas.curve(W / 2, top + card_h + 4, x + width2 / 2, y2 - 6)

    y3 = y2 + card_h + 56
    if grandchildren:
        width3 = (W - 52 - gap * (len(grandchildren) - 1)) / len(grandchildren)
        for index, node in enumerate(grandchildren):
            x = 26 + index * (width3 + gap)
            canvas.card(x, y3, width3, card_h - 14, node["title"],
                        node.get("sub", ""),
                        C.get(node.get("colour", "sky"), C["sky"]),
                        node.get("body", []), node.get("tag", ""))
            parent = node.get("parent", 0)
            px = 26 + parent * (width2 + gap) + width2 / 2
            canvas.curve(px, y2 + card_h + 4, x + width3 / 2, y3 - 6)
        y3 += card_h - 14

    y = (y3 if grandchildren else y2 + card_h) + 22
    for text in notes:
        y += canvas.note(26, y, W - 52, text) + 10
    canvas.height = int(y + 20)
    return canvas.svg(spec["title"], spec.get("subtitle", ""))


def _layout_columns(spec: dict) -> str:
    columns = spec["columns"]
    notes = spec.get("notes", [])
    gap = 22
    width = (W - 52 - gap * (len(columns) - 1)) / len(columns)
    rows = max(len(c.get("items", [])) for c in columns)
    height = 92 + 46 + rows * 30 + 30
    canvas = Canvas(height=int(height + 70 * len(notes)))

    top = 84
    for index, column in enumerate(columns):
        x = 26 + index * (width + gap)
        colour = C.get(column.get("colour", "sky"), C["sky"])
        body_h = 46 + rows * 30
        canvas.rect(x, top, width, body_h, fill=CARD2, stroke=colour, rx=13)
        canvas.rect(x, top, width, 34, fill="#1b2942", stroke=colour, rx=13,
                    width=1.2)
        canvas.text(x + width / 2, top + 22, column["title"], size=12.5,
                    weight="700", anchor="middle")
        y = top + 56
        for item in column.get("items", []):
            canvas.parts.append(
                f'<circle cx="{x + 16:.1f}" cy="{y - 4:.1f}" r="3" fill="{colour}"/>')
            for line_index, line in enumerate(_wrap(item, int(width / 5.9))):
                canvas.text(x + 28, y + line_index * 13, line, size=10.5,
                            fill="#cbd5e1")
                if line_index:
                    y += 13
            y += 30
        canvas.height = max(canvas.height, int(y + 40))

    y = top + 46 + rows * 30 + 26
    for text in notes:
        y += canvas.note(26, y, W - 52, text) + 10
    canvas.height = int(max(canvas.height, y + 20))
    return canvas.svg(spec["title"], spec.get("subtitle", ""))


def _layout_layers(spec: dict) -> str:
    layers = spec["layers"]
    notes = spec.get("notes", [])
    canvas = Canvas(height=600)
    y = 84
    for layer in layers:
        items = layer["items"]
        gap = 20
        width = (W - 96 - gap * (len(items) - 1)) / max(1, len(items))
        card_h = layer.get("card_h", 74)
        band_h = card_h + 56
        canvas.band(26, y, W - 52, band_h, layer["title"],
                    C.get(layer.get("colour", "slate"), C["slate"]))
        for index, item in enumerate(items):
            x = 52 + index * (width + gap)
            canvas.card(x, y + 34, width, card_h, item["title"],
                        item.get("sub", ""),
                        C.get(item.get("colour", layer.get("colour", "sky")),
                              C["sky"]),
                        item.get("body", []), item.get("tag", ""))
        y += band_h + 20
        if layer.get("arrow"):
            canvas.line(W / 2, y - 16, W / 2, y + 2)
    for text in notes:
        y += canvas.note(26, y, W - 52, text) + 10
    canvas.height = int(y + 16)
    return canvas.svg(spec["title"], spec.get("subtitle", ""))


def _layout_timeline(spec: dict) -> str:
    phases = spec["phases"]
    notes = spec.get("notes", [])
    canvas = Canvas(height=340 + 70 * len(notes))
    top = 108
    gap = 18
    width = (W - 52 - gap * (len(phases) - 1)) / len(phases)
    canvas.line(26, top - 22, W - 26, top - 22, stroke=STROKE, width=2,
                arrow=True)
    canvas.text(W - 26, top - 32, "time", size=10, fill=MUTED, anchor="end")
    for index, phase in enumerate(phases):
        x = 26 + index * (width + gap)
        colour = C.get(phase.get("colour", "sky"), C["sky"])
        canvas.parts.append(
            f'<circle cx="{x + width / 2:.1f}" cy="{top - 22:.1f}" r="6" '
            f'fill="{colour}"/>')
        canvas.card(x, top, width, 128, phase["title"], phase.get("sub", ""),
                    colour, phase.get("body", []), phase.get("tag", ""))
    y = top + 148
    for text in notes:
        y += canvas.note(26, y, W - 52, text) + 10
    canvas.height = int(y + 16)
    return canvas.svg(spec["title"], spec.get("subtitle", ""))


LAYOUTS = {"chain": _layout_chain, "tree": _layout_tree, "columns": _layout_columns,
           "layers": _layout_layers, "timeline": _layout_timeline}


# ---------------------------------------------------------------------------
# the diagrams
# ---------------------------------------------------------------------------
SPECS: Dict[str, dict] = {

"introduction": {"layout": "columns", "title": "What Kubernetes gives you",
 "subtitle": "an orchestrator turns 'containers on machines' into 'a declared system'",
 "columns": [
   {"title": "You declare", "colour": "blue", "items": [
     "how many replicas", "which image and version", "how much CPU and memory",
     "how traffic reaches it", "which storage it needs"]},
   {"title": "Kubernetes maintains", "colour": "green", "items": [
     "restarts crashed containers", "reschedules pods off dead nodes",
     "load balances across replicas", "rolls updates out gradually",
     "rolls them back when they fail"]},
   {"title": "You stop doing", "colour": "amber", "items": [
     "sshing to a box to restart things", "hand-editing load balancer config",
     "guessing which server has room", "bespoke deploy scripts per app"]}],
 "notes": ["Declarative, not imperative: you describe the desired state and a "
           "controller loop closes the gap, over and over, forever."]},

"architecture": {"layout": "layers", "title": "Cluster architecture",
 "subtitle": "the control plane decides what should run; the nodes actually run it",
 "layers": [
   {"title": "CONTROL PLANE", "colour": "violet", "arrow": True, "items": [
     {"title": "kube-apiserver", "sub": "the only front door",
      "body": ["authn -> authz -> admission"], "colour": "violet"},
     {"title": "etcd", "sub": "cluster state", "body": ["every object lives here"],
      "colour": "teal"},
     {"title": "scheduler", "sub": "placement", "body": ["filter, then score"],
      "colour": "cyan"},
     {"title": "controller-manager", "sub": "reconcile loops",
      "body": ["desired vs observed"], "colour": "indigo"}]},
   {"title": "WORKER NODE (x N)", "colour": "sky", "items": [
     {"title": "kubelet", "sub": "node agent", "body": ["makes pods actually run"],
      "colour": "sky"},
     {"title": "kube-proxy", "sub": "service networking",
      "body": ["iptables / IPVS rules"], "colour": "green"},
     {"title": "container runtime", "sub": "containerd / CRI-O",
      "body": ["pulls images, starts them"], "colour": "amber"},
     {"title": "your Pods", "sub": "the actual workload", "body": ["one IP each"],
      "colour": "pink"}]}],
 "notes": ["Every arrow in a cluster goes through the API server. Nothing talks "
           "to etcd directly except the API server itself."]},

"pods": {"layout": "chain", "title": "A Pod", "per_row": 3, "card_h": 112,
 "subtitle": "one or more containers that share a network namespace, storage and a lifecycle",
 "steps": [
   {"title": "Pod", "sub": "the scheduling unit", "colour": "sky",
    "body": ["one IP address", "one node", "shared volumes"], "edge": "contains"},
   {"title": "container: app", "sub": "nginx:1.25", "colour": "blue",
    "body": ["listens on :80", "requests + limits", "probes"], "edge": "localhost"},
   {"title": "container: sidecar", "sub": "log shipper", "colour": "violet",
    "body": ["same IP as app", "reads the shared volume"]}],
 "notes": ["Pods are disposable. Never rely on a pod's name or IP -- put a "
           "controller in front (Deployment) and a Service on top."]},

"deployments": {"layout": "tree", "title": "Deployment -> ReplicaSet -> Pod",
 "subtitle": "the ownership chain, and what a rolling update actually does",
 "root": {"title": "Deployment", "sub": "declares the pod template + replica count",
          "colour": "blue", "tag": "apps/v1"},
 "children": [
   {"title": "ReplicaSet (rev 1)", "sub": "old template", "colour": "indigo",
    "body": ["scaled down as new pods pass", "kept, so undo works"], "tag": "0/3"},
   {"title": "ReplicaSet (rev 2)", "sub": "new template", "colour": "violet",
    "body": ["scaled up first (maxSurge)", "this is the live one"], "tag": "3/3"}],
 "grandchildren": [
   {"title": "Pod", "sub": "terminating", "colour": "slate", "parent": 0},
   {"title": "Pod", "sub": "Running", "colour": "sky", "parent": 1},
   {"title": "Pod", "sub": "Running", "colour": "sky", "parent": 1},
   {"title": "Pod", "sub": "ContainerCreating", "colour": "amber", "parent": 1}],
 "notes": ["Change the pod template and a new ReplicaSet is created -- that is a "
           "revision. `kubectl rollout undo` simply scales the previous one back up.",
           "spec.selector.matchLabels MUST equal spec.template.metadata.labels, and "
           "the selector is immutable once created."]},

"replicasets": {"layout": "chain", "title": "The ReplicaSet control loop",
 "per_row": 4, "card_h": 92,
 "subtitle": "the simplest reconcile loop in Kubernetes, running forever",
 "steps": [
   {"title": "desired", "sub": "spec.replicas = 3", "colour": "blue",
    "edge": "compare"},
   {"title": "observed", "sub": "count pods matching the selector",
    "colour": "cyan", "edge": "differ?"},
   {"title": "act", "sub": "create or delete pods", "colour": "amber",
    "edge": "repeat"},
   {"title": "status", "sub": "replicas / readyReplicas", "colour": "green"}],
 "notes": ["Delete a pod and the loop notices within a second and makes another. "
           "That is why `kubectl delete pod` never reduces a Deployment."]},

"services": {"layout": "columns", "title": "Service types",
 "subtitle": "a stable virtual IP and DNS name in front of a moving set of pods",
 "columns": [
   {"title": "ClusterIP (default)", "colour": "cyan", "items": [
     "reachable inside the cluster only", "stable virtual IP",
     "DNS: name.namespace.svc.cluster.local", "the building block for the rest"]},
   {"title": "NodePort", "colour": "green", "items": [
     "opens the same port on every node", "port range 30000-32767",
     "still has a ClusterIP underneath", "fine for dev, blunt for production"]},
   {"title": "LoadBalancer", "colour": "amber", "items": [
     "asks the cloud for an external IP", "one LB per Service (it adds up)",
     "NodePort + ClusterIP underneath"]},
   {"title": "Headless / ExternalName", "colour": "violet", "items": [
     "clusterIP: None -> DNS returns pod IPs", "used by StatefulSets",
     "ExternalName is a CNAME out of the cluster"]}],
 "notes": ["A Service finds pods by LABEL SELECTOR, and only includes pods that are "
           "Ready. No endpoints almost always means the selector does not match, or "
           "the readiness probe is failing."]},

"ingress": {"layout": "chain", "title": "Ingress routing", "per_row": 4,
 "card_h": 104,
 "subtitle": "one entry point, host- and path-based rules, TLS terminated at the edge",
 "steps": [
   {"title": "client", "sub": "https://shop.example.com/api", "colour": "slate",
    "edge": "DNS + LB"},
   {"title": "Ingress Controller", "sub": "nginx / traefik / envoy pod",
    "colour": "amber", "body": ["watches Ingress objects", "programs the proxy"],
    "edge": "matches rule"},
   {"title": "Ingress rule", "sub": "host + path -> backend", "colour": "yellow",
    "body": ["/     -> web:80", "/api  -> api:8080"], "edge": "backend"},
   {"title": "Service -> Pods", "sub": "ClusterIP + endpoints", "colour": "cyan",
    "body": ["load balanced", "only Ready pods"]}],
 "notes": ["An Ingress object on its own does nothing. Without a controller running "
           "in the cluster it is just a row in etcd."]},

"ingress-controller": {"layout": "chain", "title": "What the Ingress Controller does",
 "per_row": 3, "card_h": 104,
 "steps": [
   {"title": "watch", "sub": "the API server", "colour": "violet",
    "body": ["Ingress, Service, Endpoints", "Secrets for TLS"], "edge": "on change"},
   {"title": "render config", "sub": "nginx.conf / envoy xDS", "colour": "amber",
    "body": ["server blocks per host", "upstreams per Service"], "edge": "reload"},
   {"title": "serve traffic", "sub": "the proxy pod itself", "colour": "green",
    "body": ["TLS termination", "rewrites, rate limits, auth"]}],
 "notes": ["ingressClassName decides which controller owns an Ingress, so several "
           "controllers can coexist in one cluster."]},

"configmaps": {"layout": "chain", "title": "ConfigMaps and Secrets",
 "per_row": 3, "card_h": 118,
 "subtitle": "keep configuration out of the image so one image runs in every environment",
 "steps": [
   {"title": "ConfigMap / Secret", "sub": "key -> value", "colour": "yellow",
    "body": ["APP_PORT: 8080", "app.properties: |", "password: (base64)"],
    "edge": "referenced by"},
   {"title": "Pod spec", "sub": "env / envFrom / volumes", "colour": "sky",
    "body": ["env.valueFrom.configMapKeyRef", "envFrom.configMapRef",
             "volumes[].configMap"], "edge": "surfaces as"},
   {"title": "Inside the container", "sub": "what the app sees", "colour": "green",
    "body": ["$APP_PORT in the environment",
             "/etc/config/app.properties on disk"]}],
 "notes": ["Mounted files update when the ConfigMap changes; environment variables "
           "do NOT -- the pod must restart.",
           "Secrets are base64-encoded, not encrypted. Anyone who can read Secrets "
           "can read your passwords: enable encryption at rest and lock down RBAC."]},

"namespaces": {"layout": "layers", "title": "Namespaces and quotas",
 "subtitle": "virtual clusters inside one cluster, with hard limits per team",
 "layers": [
   {"title": "CLUSTER-SCOPED (not in any namespace)", "colour": "violet", "items": [
     {"title": "Node", "colour": "slate"}, {"title": "PersistentVolume", "colour": "teal"},
     {"title": "StorageClass", "colour": "sky"}, {"title": "ClusterRole", "colour": "rose"},
     {"title": "CRD", "colour": "purple"}], "card_h": 46},
   {"title": "namespace: dev   (ResourceQuota: 10 pods, 2 cpu, 4Gi)",
    "colour": "green", "items": [
     {"title": "Deployment", "colour": "blue"}, {"title": "Service", "colour": "cyan"},
     {"title": "ConfigMap", "colour": "yellow"}, {"title": "PVC", "colour": "teal"}],
    "card_h": 46},
   {"title": "namespace: prod  (ResourceQuota: 40 pods, 16 cpu, 64Gi)",
    "colour": "amber", "items": [
     {"title": "Deployment", "colour": "blue"}, {"title": "Service", "colour": "cyan"},
     {"title": "Ingress", "colour": "amber"}, {"title": "PDB", "colour": "pink"}],
    "card_h": 46}],
 "notes": ["Names are unique per namespace, not per cluster. Deleting a namespace "
           "deletes everything inside it.",
           "ResourceQuota caps the totals; LimitRange sets the per-container defaults."]},

"pv": {"layout": "layers", "title": "The storage stack",
 "subtitle": "who asks, who provides, and what actually holds the bytes",
 "layers": [
   {"title": "THE APPLICATION", "colour": "sky", "arrow": True, "items": [
     {"title": "Pod", "sub": "volumes[].persistentVolumeClaim", "colour": "sky",
      "body": ["mounts it at a path"]}]},
   {"title": "THE REQUEST", "colour": "teal", "arrow": True, "items": [
     {"title": "PersistentVolumeClaim", "sub": "'I need 5Gi, RWO, class standard'",
      "colour": "teal", "body": ["namespaced, lives with the app"]}]},
   {"title": "THE SUPPLY", "colour": "green", "items": [
     {"title": "PersistentVolume", "sub": "the actual disk", "colour": "green",
      "body": ["cluster-scoped", "capacity + accessModes", "reclaim policy"]},
     {"title": "StorageClass", "sub": "how to make one on demand", "colour": "blue",
      "body": ["provisioner (CSI driver)", "parameters, binding mode"]}]}],
 "notes": ["Static: an admin pre-creates PVs and a claim binds to one that fits. "
           "Dynamic: a StorageClass creates the PV the moment the claim appears.",
           "A pod whose PVC is not Bound stays Pending -- describe the PVC, not the pod."]},

"pvc": {"layout": "timeline", "title": "PVC lifecycle",
 "subtitle": "Pending -> Bound -> In use -> Released",
 "phases": [
   {"title": "Pending", "sub": "claim created", "colour": "amber",
    "body": ["waiting for a matching PV", "or for dynamic provisioning",
             "or for a first consumer"]},
   {"title": "Bound", "sub": "matched to a PV", "colour": "green",
    "body": ["1:1 with that volume", "capacity + modes satisfied"]},
   {"title": "In use", "sub": "mounted by a pod", "colour": "sky",
    "body": ["deletion is blocked", "while a pod still uses it"]},
   {"title": "Released", "sub": "claim deleted", "colour": "violet",
    "body": ["Retain: data kept, manual cleanup", "Delete: the disk goes too"]}],
 "notes": ["Access modes are per NODE, not per pod: RWO means one node can mount it "
           "read-write, even if several pods on that node share it."]},

"storage-classes": {"layout": "columns", "title": "StorageClass",
 "subtitle": "a named recipe for making volumes on demand",
 "columns": [
   {"title": "provisioner", "colour": "blue", "items": [
     "which CSI driver creates the disk", "ebs.csi.aws.com, pd.csi.storage.gke.io",
     "local hostpath in this lab"]},
   {"title": "parameters", "colour": "cyan", "items": [
     "disk type: gp3, ssd, standard", "fsType: ext4 / xfs",
     "IOPS, throughput, encryption"]},
   {"title": "volumeBindingMode", "colour": "amber", "items": [
     "Immediate: bind as soon as the claim exists",
     "WaitForFirstConsumer: wait for a pod, then create the disk in the right zone"]},
   {"title": "reclaimPolicy", "colour": "green", "items": [
     "Delete: disk removed with the claim",
     "Retain: disk survives, admin cleans up", "the default class is used when a "
     "PVC names none"]}]},

"dynamic-provisioning": {"layout": "chain", "title": "Dynamic provisioning",
 "per_row": 4, "card_h": 100,
 "subtitle": "no administrator in the loop",
 "steps": [
   {"title": "PVC created", "sub": "storageClassName: fast-ssd", "colour": "teal",
    "edge": "watched by"},
   {"title": "StorageClass", "sub": "names a provisioner", "colour": "blue",
    "edge": "calls"},
   {"title": "CSI driver", "sub": "cloud API / local", "colour": "violet",
    "body": ["creates a real disk"], "edge": "creates"},
   {"title": "PV + Bound", "sub": "claim satisfied", "colour": "green",
    "body": ["pod can start"]}]},

"statefulsets": {"layout": "tree", "title": "StatefulSet",
 "subtitle": "stable identity, stable storage, ordered rollout",
 "root": {"title": "StatefulSet: db", "sub": "serviceName: db (headless)",
          "colour": "violet", "tag": "replicas 3"},
 "children": [
   {"title": "db-0", "sub": "created first", "colour": "sky",
    "body": ["DNS: db-0.db.ns.svc", "PVC: data-db-0"]},
   {"title": "db-1", "sub": "only after db-0 is Ready", "colour": "sky",
    "body": ["DNS: db-1.db.ns.svc", "PVC: data-db-1"]},
   {"title": "db-2", "sub": "only after db-1 is Ready", "colour": "sky",
    "body": ["DNS: db-2.db.ns.svc", "PVC: data-db-2"]}],
 "notes": ["Scale down and the PVCs stay behind on purpose -- that is the whole "
           "point. Deleting the StatefulSet does not delete its data either.",
           "Deployment vs StatefulSet: pick StatefulSet only when the replicas are "
           "NOT interchangeable (databases, queues, consensus systems)."]},

"daemonsets": {"layout": "layers", "title": "DaemonSet",
 "subtitle": "exactly one pod per (selected) node, automatically",
 "layers": [
   {"title": "DaemonSet: node-exporter", "colour": "pink", "arrow": True, "items": [
     {"title": "one pod template", "sub": "tolerations: Exists", "colour": "pink",
      "body": ["so it also lands on tainted nodes"]}]},
   {"title": "EVERY NODE GETS ONE", "colour": "sky", "items": [
     {"title": "control-plane", "sub": "pod running", "colour": "violet"},
     {"title": "worker-1", "sub": "pod running", "colour": "sky"},
     {"title": "worker-2", "sub": "pod running", "colour": "sky"},
     {"title": "worker-3 (new)", "sub": "pod added on join", "colour": "green"}],
    "card_h": 62}],
 "notes": ["Typical uses: log shippers (Fluent Bit), metrics agents (node-exporter), "
           "CNI and CSI node plugins, security agents.",
           "Add a nodeSelector to target a subset; add tolerations to reach tainted "
           "nodes."]},

"jobs": {"layout": "columns", "title": "Job", "subtitle": "run to completion, then stop",
 "columns": [
   {"title": "completions", "colour": "green", "items": [
     "how many pods must succeed", "completions: 3 -> three successful runs"]},
   {"title": "parallelism", "colour": "cyan", "items": [
     "how many run at the same time", "parallelism: 2 -> two at a time"]},
   {"title": "backoffLimit", "colour": "amber", "items": [
     "retries before the Job is Failed", "each retry is a fresh pod"]},
   {"title": "restartPolicy", "colour": "violet", "items": [
     "must be OnFailure or Never", "Always is rejected -- a Job is meant to end"]}],
 "notes": ["Finished pods are kept so you can read their logs. ttlSecondsAfterFinished "
           "cleans them up automatically."]},

"cronjobs": {"layout": "chain", "title": "CronJob", "per_row": 4, "card_h": 96,
 "subtitle": "a Job factory on a schedule",
 "steps": [
   {"title": "schedule", "sub": '"0 2 * * *"', "colour": "emerald",
    "body": ["min hour dom mon dow"], "edge": "fires"},
   {"title": "creates a Job", "sub": "from jobTemplate", "colour": "green",
    "edge": "which creates"},
   {"title": "Pods", "sub": "run to completion", "colour": "sky", "edge": "kept"},
   {"title": "history", "sub": "successfulJobsHistoryLimit", "colour": "slate",
    "body": ["old Jobs pruned"]}],
 "notes": ["concurrencyPolicy: Allow (default) | Forbid (skip if the last one is "
           "still running) | Replace (kill it and start fresh).",
           "suspend: true pauses the schedule without deleting anything."]},

"init-containers": {"layout": "timeline", "title": "Init containers",
 "subtitle": "run to completion, in order, before any app container starts",
 "phases": [
   {"title": "init 1", "sub": "wait-for-db", "colour": "amber",
    "body": ["blocks until DNS resolves", "pod shows Init:0/2"]},
   {"title": "init 2", "sub": "run-migrations", "colour": "amber",
    "body": ["schema applied once", "pod shows Init:1/2"]},
   {"title": "app containers", "sub": "start together", "colour": "sky",
    "body": ["all of them, in parallel", "probes begin"]},
   {"title": "Ready", "sub": "readiness passes", "colour": "green",
    "body": ["added to Service endpoints"]}],
 "notes": ["If an init container fails the kubelet restarts the pod and starts the "
           "sequence again -- so init work must be idempotent."]},

"multi-container": {"layout": "columns", "title": "Multi-container pod patterns",
 "subtitle": "containers that share an IP, a localhost and a volume",
 "columns": [
   {"title": "Sidecar", "colour": "violet", "items": [
     "helper next to the app", "log shipper reading a shared emptyDir",
     "service-mesh proxy", "the most common pattern by far"]},
   {"title": "Ambassador", "colour": "cyan", "items": [
     "proxies the app's OUTBOUND calls", "app talks to localhost:6379",
     "ambassador handles sharding / TLS"]},
   {"title": "Adapter", "colour": "amber", "items": [
     "reshapes the app's output", "turns bespoke logs into /metrics",
     "so the platform can consume it"]}],
 "notes": ["Only put containers in the same pod when they must share a lifecycle, a "
           "network namespace or a disk. Otherwise use two pods and a Service."]},

"labels": {"layout": "chain", "title": "Labels and selectors", "per_row": 3,
 "card_h": 112,
 "subtitle": "the string-matching that holds the whole system together",
 "steps": [
   {"title": "labels on objects", "sub": "key=value metadata", "colour": "yellow",
    "body": ["app=web", "tier=frontend", "env=prod"], "edge": "queried by"},
   {"title": "selectors", "sub": "equality or set-based", "colour": "cyan",
    "body": ["app=web", "env in (prod,staging)", "!legacy"], "edge": "used by"},
   {"title": "who selects", "sub": "everything, basically", "colour": "sky",
    "body": ["Service -> its pods", "ReplicaSet -> its pods",
             "NetworkPolicy, PDB, affinity"]}],
 "notes": ["Recommended set: app.kubernetes.io/name, /instance, /version, "
           "/component, /part-of, /managed-by.",
           "Labels are for SELECTION. Anything you never select on should be an "
           "annotation instead."]},

"annotations": {"layout": "columns", "title": "Annotations",
 "subtitle": "metadata for tools and humans, never for selection",
 "columns": [
   {"title": "What goes here", "colour": "yellow", "items": [
     "build number, git SHA, owner", "kubernetes.io/change-cause",
     "controller configuration", "free-form docs and links"]},
   {"title": "Who reads them", "colour": "cyan", "items": [
     "Ingress controllers (rewrites, auth)", "cert-manager (issuer)",
     "Prometheus (scrape config)", "your own tooling"]},
   {"title": "vs labels", "colour": "violet", "items": [
     "no selector can match an annotation", "no length or character limits",
     "not indexed by the API server"]}]},

"taints": {"layout": "chain", "title": "Taints and tolerations", "per_row": 3,
 "card_h": 118,
 "subtitle": "a node REPELS pods; a toleration lets one through",
 "steps": [
   {"title": "taint on the node", "sub": "dedicated=payments:NoSchedule",
    "colour": "red", "body": ["NoSchedule: keep new pods off",
                              "PreferNoSchedule: soft",
                              "NoExecute: also evict running pods"],
    "edge": "blocks"},
   {"title": "pod without a toleration", "sub": "stays Pending", "colour": "slate",
    "body": ["0/4 nodes are available:", "3 had untolerated taint"], "edge": "unless"},
   {"title": "pod with a toleration", "sub": "may be scheduled there",
    "colour": "green", "body": ["key + value + effect match",
                                "operator: Equal or Exists"]}],
 "notes": ["A toleration only ALLOWS -- it does not attract. To pull a pod towards "
           "specific nodes you also need nodeSelector or node affinity.",
           "Control-plane nodes carry a NoSchedule taint by default; that is why "
           "your workloads never land there."]},

"node-affinity": {"layout": "columns", "title": "Node affinity",
 "subtitle": "choose nodes by their labels, with hard and soft rules",
 "columns": [
   {"title": "nodeSelector", "colour": "slate", "items": [
     "the simple version", "exact key=value matches only", "hard requirement"]},
   {"title": "required...", "colour": "red", "items": [
     "hard rule: no match, pod stays Pending",
     "operators In, NotIn, Exists, DoesNotExist, Gt, Lt",
     "several terms are OR'd, expressions inside a term are AND'd"]},
   {"title": "preferred...", "colour": "green", "items": [
     "soft rule with a weight 1-100",
     "scheduler adds the weight to that node's score",
     "pod still schedules if nothing matches"]}],
 "notes": ["'IgnoredDuringExecution' means the rule is only checked at scheduling "
           "time -- relabel the node later and the pod stays where it is."]},

"pod-affinity": {"layout": "columns", "title": "Pod affinity and anti-affinity",
 "subtitle": "place pods relative to OTHER pods, within a topology domain",
 "columns": [
   {"title": "podAffinity", "colour": "cyan", "items": [
     "put me NEAR pods matching this selector",
     "co-locate a cache with its app",
     "same node / same zone, per topologyKey"]},
   {"title": "podAntiAffinity", "colour": "amber", "items": [
     "keep me AWAY from those pods",
     "one replica per node: topologyKey kubernetes.io/hostname",
     "one replica per zone: topology.kubernetes.io/zone"]},
   {"title": "topologyKey", "colour": "violet", "items": [
     "the node label that defines 'same place'",
     "hostname -> per machine", "zone -> per availability zone",
     "required rules can leave pods Pending if the cluster is too small"]}]},

"resources": {"layout": "columns", "title": "Requests, limits and QoS",
 "subtitle": "requests are what the scheduler reserves; limits are the ceiling the kernel enforces",
 "columns": [
   {"title": "requests", "colour": "blue", "items": [
     "used for scheduling decisions", "reserved on the node",
     "no requests -> the scheduler assumes ~0 and overcommits"]},
   {"title": "limits", "colour": "amber", "items": [
     "CPU over limit -> throttled (slow)",
     "memory over limit -> OOMKilled (dead)",
     "CPU is compressible, memory is not"]},
   {"title": "QoS class", "colour": "green", "items": [
     "Guaranteed: requests == limits everywhere",
     "Burstable: requests set, limits higher",
     "BestEffort: nothing set -- evicted first"]}],
 "notes": ["Size from real usage: `kubectl top pods` next to the requests you wrote. "
           "A VerticalPodAutoscaler in recommend-only mode does this for you."]},

"probes": {"layout": "timeline", "title": "Startup, readiness and liveness probes",
 "subtitle": "three questions the kubelet asks, at three different times",
 "phases": [
   {"title": "startupProbe", "sub": "'have you booted yet?'", "colour": "violet",
    "body": ["disables the other two", "generous failureThreshold",
             "for slow JVM / migration starts"]},
   {"title": "readinessProbe", "sub": "'can you serve traffic?'", "colour": "cyan",
    "body": ["fail -> removed from endpoints", "pod keeps running",
             "this is your rollout gate"]},
   {"title": "livenessProbe", "sub": "'are you still alive?'", "colour": "red",
    "body": ["fail -> container restarted", "too aggressive = restart loop"]},
   {"title": "steady state", "sub": "Ready 1/1", "colour": "green",
    "body": ["in the Service endpoints", "receiving traffic"]}],
 "notes": ["Handlers: httpGet, tcpSocket, exec. Tune with initialDelaySeconds, "
           "periodSeconds, timeoutSeconds, failureThreshold.",
           "Swapping readiness and liveness is a classic outage: a slow dependency "
           "should take you out of the load balancer, not kill your container."]},

"hpa": {"layout": "chain", "title": "Horizontal Pod Autoscaler", "per_row": 4,
 "card_h": 100,
 "subtitle": "more pods when the metric rises, fewer when it falls",
 "steps": [
   {"title": "metrics-server", "sub": "current CPU per pod", "colour": "violet",
    "edge": "reads"},
   {"title": "HPA", "sub": "target 50% of requests", "colour": "purple",
    "body": ["desired = ceil(current x", "  usage / target)"], "edge": "scales"},
   {"title": "Deployment", "sub": "spec.replicas changes", "colour": "blue",
    "edge": "creates"},
   {"title": "more Pods", "sub": "min <= n <= max", "colour": "sky",
    "body": ["load per pod drops"]}],
 "notes": ["No CPU requests on the containers means no utilisation percentage, which "
           "means the HPA shows <unknown> and never acts.",
           "Scale-up is fast, scale-down is deliberately slow (stabilisation window) "
           "so traffic dips do not cause flapping."]},

"vpa": {"layout": "columns", "title": "Vertical Pod Autoscaler",
 "subtitle": "right-size one pod instead of adding more of them",
 "columns": [
   {"title": "Off (recommend)", "colour": "green", "items": [
     "watches real usage", "writes a recommendation you can read",
     "safest, and usually enough"]},
   {"title": "Initial", "colour": "cyan", "items": [
     "applies the recommendation to NEW pods only",
     "no disruption to running ones"]},
   {"title": "Auto", "colour": "amber", "items": [
     "evicts and recreates pods with new requests",
     "disruptive -- needs a PodDisruptionBudget"]}],
 "notes": ["Do not point a VPA and an HPA at the same CPU metric: they will fight. "
           "HPA on CPU + VPA on memory is a common safe split."]},

"cluster-autoscaler": {"layout": "chain", "title": "Cluster Autoscaler",
 "per_row": 4, "card_h": 96,
 "subtitle": "the layer below the HPA: more NODES, not more pods",
 "steps": [
   {"title": "Pending pod", "sub": "Insufficient cpu", "colour": "amber",
    "edge": "noticed"},
   {"title": "Cluster Autoscaler", "sub": "simulates placement", "colour": "violet",
    "body": ["would a new node help?"], "edge": "asks cloud"},
   {"title": "node group +1", "sub": "instance boots and joins", "colour": "blue",
    "edge": "scheduler"},
   {"title": "pod scheduled", "sub": "cluster settles", "colour": "green",
    "body": ["idle nodes drained later"]}],
 "notes": ["Scale-down removes nodes that stay underutilised AND whose pods can move "
           "elsewhere, respecting PodDisruptionBudgets."]},

"network-policies": {"layout": "chain", "title": "NetworkPolicy", "per_row": 3,
 "card_h": 116,
 "subtitle": "by default every pod can reach every pod; a policy makes that selective",
 "steps": [
   {"title": "no policy", "sub": "flat network", "colour": "slate",
    "body": ["web -> api  allowed", "web -> db   allowed", "anything -> anything"],
    "edge": "add one"},
   {"title": "policy selects a pod", "sub": "podSelector: app=api",
    "colour": "amber", "body": ["that pod becomes DEFAULT-DENY",
                                "for the listed policyTypes"], "edge": "then"},
   {"title": "only listed peers pass", "sub": "from: podSelector app=web",
    "colour": "green", "body": ["web -> api:8080 allowed",
                                "everything else denied"]}],
 "notes": ["Policies are additive and there is no deny rule: traffic is allowed if "
           "ANY policy allows it.",
           "Your CNI must enforce policy (Calico, Cilium, Antrea). With a plugin "
           "that ignores them, the objects exist and do nothing."]},

"rbac": {"layout": "chain", "title": "RBAC", "per_row": 4, "card_h": 108,
 "subtitle": "who (subject) may do what (verb) to which resource, where (scope)",
 "steps": [
   {"title": "Subject", "sub": "User / Group / ServiceAccount", "colour": "rose",
    "edge": "named in"},
   {"title": "RoleBinding", "sub": "or ClusterRoleBinding", "colour": "pink",
    "body": ["subjects + one roleRef"], "edge": "points to"},
   {"title": "Role", "sub": "or ClusterRole", "colour": "violet",
    "body": ["apiGroups + resources", "+ verbs"], "edge": "grants"},
   {"title": "Permission", "sub": "get, list, create, delete…", "colour": "green",
    "body": ["deny by default", "purely additive"]}],
 "notes": ["Role + RoleBinding = one namespace. ClusterRole + ClusterRoleBinding = "
           "whole cluster. ClusterRole + RoleBinding = reuse one definition in a "
           "single namespace (very common).",
           "There is no deny rule. If nothing grants it, it is denied."]},

"service-accounts": {"layout": "chain", "title": "ServiceAccounts", "per_row": 3,
 "card_h": 112,
 "subtitle": "the identity a POD uses when it calls the API server",
 "steps": [
   {"title": "ServiceAccount", "sub": "namespaced identity", "colour": "yellow",
    "body": ["every namespace has 'default'"], "edge": "referenced by"},
   {"title": "Pod", "sub": "spec.serviceAccountName", "colour": "sky",
    "body": ["token projected into", "/var/run/secrets/..."], "edge": "authenticates"},
   {"title": "API server", "sub": "system:serviceaccount:ns:name",
    "colour": "violet", "body": ["then RBAC decides", "what it may do"]}],
 "notes": ["Set automountServiceAccountToken: false on pods that never call the API "
           "-- most of them.",
           "Give each workload its own ServiceAccount; never reuse 'default' for "
           "anything privileged."]},

"authentication": {"layout": "columns", "title": "Authentication",
 "subtitle": "'who are you?' -- there are no User objects in Kubernetes",
 "columns": [
   {"title": "x509 client cert", "colour": "blue", "items": [
     "CN becomes the username", "O becomes the groups",
     "what kubeadm gives your admin kubeconfig"]},
   {"title": "ServiceAccount token", "colour": "yellow", "items": [
     "JWT signed by the API server", "for in-cluster workloads",
     "short-lived and audience-bound"]},
   {"title": "OIDC / webhook", "colour": "violet", "items": [
     "your company identity provider", "groups come from the token claims",
     "how humans should authenticate"]}],
 "notes": ["Users are external to Kubernetes: it validates a credential and extracts "
           "a name and groups. That is why you cannot `kubectl create user`."]},

"authorization": {"layout": "chain", "title": "The API request path", "per_row": 5,
 "card_h": 104,
 "subtitle": "every request runs this gauntlet before anything is stored",
 "steps": [
   {"title": "1 Authentication", "sub": "who are you?", "colour": "blue",
    "edge": "identity"},
   {"title": "2 Authorization", "sub": "may you?", "colour": "violet",
    "body": ["RBAC (default)", "Node, Webhook, ABAC"], "edge": "allowed"},
   {"title": "3 Mutating admission", "sub": "change it", "colour": "amber",
    "body": ["defaults, sidecars,", "labels"], "edge": "object"},
   {"title": "4 Validating admission", "sub": "accept or reject", "colour": "red",
    "body": ["quotas, policy engines"], "edge": "valid"},
   {"title": "5 etcd", "sub": "persisted", "colour": "green",
    "body": ["controllers take over"]}],
 "notes": ["This is the single most useful diagram to have in your head during a CKA "
           "exam or a production incident."]},

"admission": {"layout": "columns", "title": "Admission controllers",
 "subtitle": "the gatekeepers between authorization and storage",
 "columns": [
   {"title": "Mutating (first)", "colour": "amber", "items": [
     "DefaultStorageClass adds a class to a PVC",
     "LimitRanger fills in default requests",
     "MutatingAdmissionWebhook injects sidecars"]},
   {"title": "Validating (second)", "colour": "red", "items": [
     "ResourceQuota rejects the pod over budget",
     "PodSecurity enforces the standards",
     "NamespaceLifecycle blocks writes to a terminating namespace"]},
   {"title": "Your own policy", "colour": "violet", "items": [
     "OPA / Gatekeeper, Kyverno",
     "'every image must come from our registry'",
     "'every workload must set limits'"]}]},

"etcd_helper": {"layout": "columns", "title": "etcd", "columns": [
   {"title": "what", "colour": "teal", "items": ["consistent key/value store",
     "the ONLY stateful part of the control plane"]}]},

"helm": {"layout": "chain", "title": "Helm", "per_row": 4, "card_h": 104,
 "subtitle": "templates + values = manifests, tracked as a named release",
 "steps": [
   {"title": "Chart", "sub": "templates/ + values.yaml", "colour": "blue",
    "body": ["reusable package"], "edge": "render"},
   {"title": "Values", "sub": "defaults + --set + -f", "colour": "cyan",
    "body": ["per-environment differences"], "edge": "produce"},
   {"title": "Manifests", "sub": "plain YAML", "colour": "sky",
    "body": ["helm template shows them"], "edge": "install"},
   {"title": "Release", "sub": "revision 1, 2, 3…", "colour": "green",
    "body": ["helm rollback goes back"]}],
 "notes": ["Chart = the package. Release = one installed instance of it. Repository "
           "= where charts are published.",
           "`helm template` before `helm install` is the habit that saves you: see "
           "the YAML before it hits the cluster."]},

"kustomize": {"layout": "layers", "title": "Kustomize",
 "subtitle": "no templating language -- a base plus per-environment patches",
 "layers": [
   {"title": "BASE (the shared truth)", "colour": "blue", "arrow": True, "items": [
     {"title": "deployment.yaml", "sub": "replicas: 1", "colour": "blue"},
     {"title": "service.yaml", "sub": "port 80", "colour": "cyan"},
     {"title": "kustomization.yaml", "sub": "lists the resources", "colour": "slate"}]},
   {"title": "OVERLAYS (what differs)", "colour": "green", "items": [
     {"title": "overlays/dev", "sub": "namePrefix dev-", "colour": "green",
      "body": ["commonLabels env=dev"]},
     {"title": "overlays/prod", "sub": "namePrefix prod-", "colour": "amber",
      "body": ["replicas: 3, pinned image tag"]}]}],
 "notes": ["Built into kubectl: `kubectl apply -k overlays/prod`. "
           "`kustomize build overlays/prod` shows you the result first."]},

"operators": {"layout": "chain", "title": "The operator pattern", "per_row": 4,
 "card_h": 104,
 "subtitle": "encode what a human operator would do, as a controller",
 "steps": [
   {"title": "CRD", "sub": "kind: PostgresCluster", "colour": "purple",
    "body": ["a new API type"], "edge": "you create"},
   {"title": "Custom resource", "sub": "size: 3, version: 16", "colour": "violet",
    "body": ["your desired state"], "edge": "watched by"},
   {"title": "Operator", "sub": "a controller with domain logic",
    "colour": "indigo", "body": ["backups, failover,", "version upgrades"],
    "edge": "manages"},
   {"title": "Real objects", "sub": "StatefulSets, Services, Jobs",
    "colour": "sky"}],
 "notes": ["Operator = CRD + controller + operational knowledge. Without the "
           "controller a CRD is just a typed row in etcd."]},

"crds": {"layout": "chain", "title": "CustomResourceDefinition", "per_row": 3,
 "card_h": 112,
 "subtitle": "extend the Kubernetes API with your own kinds",
 "steps": [
   {"title": "define", "sub": "group / version / kind", "colour": "purple",
    "body": ["plural, singular, shortNames", "scope: Namespaced or Cluster",
             "openAPI schema"], "edge": "apply"},
   {"title": "the API grows", "sub": "immediately", "colour": "violet",
    "body": ["kubectl get backups", "RBAC, labels, events all work"],
    "edge": "then"},
   {"title": "add a controller", "sub": "to make it DO something",
    "colour": "indigo", "body": ["otherwise it only stores data"]}]},

"observability": {"layout": "layers", "title": "Observability",
 "subtitle": "metrics for trends, events for causes, logs for detail",
 "layers": [
   {"title": "COLLECT", "colour": "violet", "arrow": True, "items": [
     {"title": "metrics-server", "sub": "short-term CPU/memory", "colour": "violet",
      "body": ["feeds kubectl top + HPA"]},
     {"title": "Prometheus", "sub": "scrapes /metrics", "colour": "amber",
      "body": ["time series + PromQL"]},
     {"title": "kube-state-metrics", "sub": "object state as metrics",
      "colour": "cyan"}]},
   {"title": "SEE AND ALERT", "colour": "green", "items": [
     {"title": "Grafana", "sub": "dashboards", "colour": "green"},
     {"title": "Alertmanager", "sub": "routes alerts", "colour": "red"},
     {"title": "kubectl top / events", "sub": "the fast path", "colour": "sky"}]}],
 "notes": ["The four golden signals: latency, traffic, errors, saturation.",
           "metrics-server is NOT a monitoring system -- it keeps a few minutes of "
           "data so the autoscaler can work."]},

"logging": {"layout": "chain", "title": "Logging", "per_row": 4, "card_h": 100,
 "subtitle": "write to stdout and let the platform do the rest",
 "steps": [
   {"title": "container", "sub": "stdout / stderr", "colour": "sky",
    "edge": "written to"},
   {"title": "node disk", "sub": "/var/log/pods/...", "colour": "slate",
    "body": ["what kubectl logs reads", "lost when the pod is deleted"],
    "edge": "shipped by"},
   {"title": "agent DaemonSet", "sub": "Fluent Bit / Fluentd / Promtail",
    "colour": "pink", "edge": "into"},
   {"title": "backend", "sub": "Elasticsearch / Loki", "colour": "green",
    "body": ["Kibana / Grafana on top"]}],
 "notes": ["`kubectl logs --previous` reads the last crashed container -- the single "
           "most useful flag when debugging CrashLoopBackOff.",
           "Never write logs to a file inside the container: nothing will collect "
           "them and the disk will fill."]},

"troubleshooting": {"layout": "columns", "title": "Reading a broken pod",
 "subtitle": "the status tells you which of four things went wrong",
 "columns": [
   {"title": "Pending", "colour": "slate", "items": [
     "nothing scheduled it yet", "insufficient cpu/memory",
     "untolerated taint / affinity", "PVC not Bound",
     "-> kubectl describe pod, read Events"]},
   {"title": "ImagePullBackOff", "colour": "amber", "items": [
     "wrong name or tag", "private registry, no imagePullSecret",
     "-> kubectl describe pod, check the image string"]},
   {"title": "CrashLoopBackOff", "colour": "red", "items": [
     "the container starts then exits", "config or dependency missing",
     "-> kubectl logs, then logs --previous"]},
   {"title": "Running but broken", "colour": "cyan", "items": [
     "0/1 Ready -> readiness failing", "OOMKilled -> raise memory limit",
     "Service empty -> selector mismatch",
     "-> kubectl get endpoints"]}],
 "notes": ["Order of operations: get pods -o wide  ->  describe (read the Events at "
           "the bottom)  ->  logs  ->  get endpoints  ->  auth can-i."]},

"best-practices": {"layout": "columns", "title": "Production checklist",
 "subtitle": "the difference between a demo and something you can be paged for",
 "columns": [
   {"title": "Reliability", "colour": "green", "items": [
     "3+ replicas", "PodDisruptionBudget", "anti-affinity across nodes/zones",
     "readiness + liveness probes", "rolling update, maxUnavailable 0"]},
   {"title": "Resources", "colour": "blue", "items": [
     "requests AND limits on every container", "namespace ResourceQuota",
     "LimitRange defaults", "HPA where load varies"]},
   {"title": "Security", "colour": "rose", "items": [
     "runAsNonRoot, read-only root fs", "drop capabilities",
     "least-privilege RBAC", "NetworkPolicy default-deny",
     "no :latest -- pin the tag"]},
   {"title": "Operations", "colour": "amber", "items": [
     "manifests in git, deployed by CI", "labels: name/version/part-of",
     "logs to stdout, metrics on /metrics", "tested restore, not just backup"]}]},

"interview": {"layout": "columns", "title": "CKA vs CKAD",
 "subtitle": "both are hands-on, timed and cluster-based -- speed with kubectl is the skill",
 "columns": [
   {"title": "CKA - administrator", "colour": "violet", "items": [
     "cluster architecture, install, upgrade (25%)",
     "workloads and scheduling (15%)",
     "services and networking (20%)",
     "storage (10%)",
     "troubleshooting (30%)"]},
   {"title": "CKAD - developer", "colour": "cyan", "items": [
     "application design and build (20%)",
     "deployment (20%)",
     "observability and maintenance (15%)",
     "environment, configuration, security (25%)",
     "services and networking (20%)"]},
   {"title": "Exam habits", "colour": "green", "items": [
     "alias k=kubectl, set the namespace once",
     "--dry-run=client -o yaml to generate manifests",
     "kubectl explain instead of the docs",
     "flag hard questions, come back to them"]}]},
}

SPECS["cluster-lifecycle"] = {"layout": "timeline",
 "title": "Upgrading a cluster with kubeadm",
 "subtitle": "control plane first, then one node at a time -- and never skip a minor version",
 "phases": [
   {"title": "1  plan", "sub": "kubeadm upgrade plan", "colour": "slate",
    "body": ["shows current vs target", "for every component", "and every kubelet"]},
   {"title": "2  control plane", "sub": "kubeadm upgrade apply v1.31.1",
    "colour": "violet", "body": ["apiserver, scheduler,", "controller-manager, etcd",
                                 "certs renewed too"]},
   {"title": "3  drain the node", "sub": "kubectl drain <node>", "colour": "amber",
    "body": ["--ignore-daemonsets", "workload moves elsewhere", "node is cordoned"]},
   {"title": "4  upgrade kubelet", "sub": "kubeadm upgrade node", "colour": "sky",
    "body": ["then the kubelet package", "restart the kubelet"]},
   {"title": "5  uncordon", "sub": "kubectl uncordon <node>", "colour": "green",
    "body": ["node accepts pods again", "repeat for the next node"]}],
 "notes": ["Version skew: the kubelet may be up to 3 minor versions BEHIND the API "
           "server and never ahead. That is why the control plane goes first.",
           "One minor version per hop: 1.30 -> 1.31 -> 1.32. Skipping is refused."]}

SPECS["etcd-backup"] = {"layout": "chain", "per_row": 4, "card_h": 112,
 "title": "etcd backup and restore",
 "subtitle": "the only real disaster recovery a cluster has",
 "steps": [
   {"title": "etcd", "sub": "every object lives here", "colour": "teal",
    "body": ["pods, secrets, RBAC,", "CRDs -- all of it"], "edge": "snapshot save"},
   {"title": "snapshot file", "sub": "/backup/etcd.db", "colour": "blue",
    "body": ["a point-in-time copy", "copy it OFF the node"], "edge": "status"},
   {"title": "verify", "sub": "hash, revision, keys", "colour": "cyan",
    "body": ["an unverified backup", "is not a backup"], "edge": "restore"},
   {"title": "new data dir", "sub": "--data-dir=/var/lib/etcd-new", "colour": "green",
    "body": ["point etcd at it", "restart the control plane"]}],
 "notes": ["Every etcdctl call needs ETCDCTL_API=3 plus --endpoints, --cacert, "
           "--cert and --key. Missing one looks exactly like 'etcd is down'.",
           "A restore rewinds the entire cluster to the moment of the snapshot: "
           "anything created afterwards is gone."]}

SPECS["certificates"] = {"layout": "chain", "per_row": 4, "card_h": 112,
 "title": "Adding a user: the CSR workflow",
 "subtitle": "there are no User objects -- a user is a certificate the API server trusts",
 "steps": [
   {"title": "key + CSR", "sub": "openssl, on the user's machine", "colour": "slate",
    "body": ["CN = username", "O  = groups"], "edge": "submit"},
   {"title": "CertificateSigningRequest", "sub": "an API object", "colour": "rose",
    "body": ["signerName:", "kube-apiserver-client"], "edge": "approve"},
   {"title": "kubectl certificate approve", "sub": "an admin decides",
    "colour": "violet", "body": ["status: Approved,Issued", "certificate emitted"],
    "edge": "extract"},
   {"title": "kubeconfig entry", "sub": "set-credentials + set-context",
    "colour": "green", "body": ["now they can authenticate", "RBAC decides the rest"]}],
 "notes": ["Issuing a certificate grants NOTHING. Bind a Role to that username or "
           "group or every request comes back Forbidden.",
           "kubeadm certs check-expiration -- control-plane certs last a year, and "
           "`kubeadm upgrade` renews them."]}

SPECS["kubeconfig"] = {"layout": "columns", "title": "kubeconfig",
 "subtitle": "three lists and a pointer -- read by every kubectl call",
 "columns": [
   {"title": "clusters", "colour": "blue", "items": [
     "name -> API server URL", "certificate-authority to trust it",
     "one entry per cluster you touch"]},
   {"title": "users", "colour": "rose", "items": [
     "client-certificate + client-key", "or a bearer token",
     "this is WHO you are"]},
   {"title": "contexts", "colour": "green", "items": [
     "cluster + user + namespace", "given a short name",
     "switching one changes all three"]},
   {"title": "current-context", "colour": "amber", "items": [
     "the pointer kubectl follows",
     "kubectl config use-context <name>",
     "check it before anything destructive"]}],
 "notes": ["kubectl config set-context --current --namespace=dev is how you stop "
           "typing -n on every command."]}

SPECS["images"] = {"layout": "chain", "per_row": 4, "card_h": 112,
 "title": "From build to running pod",
 "subtitle": "the node pulls over the network -- your laptop is not a registry",
 "steps": [
   {"title": "build", "sub": "docker build -t myapp:1.0 .", "colour": "slate",
    "body": ["exists locally only"], "edge": "tag"},
   {"title": "tag for a registry", "sub": "registry.lab/team/myapp:1.0",
    "colour": "cyan", "body": ["registry host in the name"], "edge": "push"},
   {"title": "push", "sub": "now reachable by nodes", "colour": "blue",
    "body": ["public or private"], "edge": "pull"},
   {"title": "kubelet pulls", "sub": "on the scheduled node", "colour": "green",
    "body": ["private -> needs", "spec.imagePullSecrets"]}],
 "notes": ["Private repo with no imagePullSecret gives ImagePullBackOff with 'pull "
           "access denied'. Create one with `kubectl create secret docker-registry`.",
           "Pin the tag. With :latest, two pods created a minute apart can run "
           "different code and a rollback means nothing."]}

# topics that share a diagram with another topic
ALIASES = {"jobs": "jobs", "etcd": "architecture"}


def has(key: str) -> bool:
    return key in SPECS


def render(key: str) -> Optional[str]:
    spec = SPECS.get(key) or SPECS.get(ALIASES.get(key, ""))
    if spec is None:
        return None
    layout = LAYOUTS[spec.get("layout", "chain")]
    return layout(spec)


def render_all() -> Dict[str, str]:
    return {key: render(key) for key in SPECS}
