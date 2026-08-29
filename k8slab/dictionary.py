"""The object dictionary: everything you created, in the order you created it.

For each object it answers three questions:

    what is this kind for?      (the handbook's one-line definition)
    what does THIS one do?      (read from its own spec)
    how did it get here?        (the command you ran, or the controller that
                                 created it on your behalf)

That last part is the interesting one: most objects in a cluster are not made by
a human, and the dictionary shows the chain -- you ran one command, and three
controllers made nine objects.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import handbook
from .model import (Cluster, annotation, deep_get, fmt_age, fmt_cpu, fmt_mem,
                    parse_cpu, parse_mem, resolve_kind)

# one line per kind: what the kind is FOR
PURPOSE: Dict[str, str] = {
    "Namespace": "A virtual cluster inside the cluster: a name scope with its own "
                 "quotas and RBAC.",
    "Node": "A machine that runs pods, with a kubelet, a kube-proxy and a container "
            "runtime on it.",
    "Pod": "The smallest deployable unit: one or more containers sharing an IP, "
           "volumes and a lifecycle.",
    "ReplicaSet": "Keeps a fixed number of identical pods running; created and owned "
                  "by a Deployment.",
    "Deployment": "Declares a pod template plus a replica count, and rolls changes "
                  "out (and back) through ReplicaSets.",
    "StatefulSet": "Like a Deployment, but each pod keeps a stable name, DNS entry "
                   "and its own storage.",
    "DaemonSet": "Runs exactly one copy of a pod on every (or every selected) node.",
    "Job": "Runs pods until a set number of them complete successfully, then stops.",
    "CronJob": "Creates Jobs on a schedule.",
    "Service": "A stable virtual IP and DNS name that load balances to whichever "
               "pods match its label selector.",
    "Ingress": "HTTP/HTTPS routing rules -- host and path to a backend Service.",
    "IngressClass": "Says which Ingress controller owns an Ingress.",
    "NetworkPolicy": "Firewall rules between pods; selecting a pod makes it "
                     "default-deny for that direction.",
    "ConfigMap": "Non-secret configuration, consumed as environment variables or "
                 "mounted files.",
    "Secret": "The same idea for sensitive values (base64-encoded, not encrypted).",
    "PersistentVolume": "A piece of real storage in the cluster, independent of any "
                        "pod's lifetime.",
    "PersistentVolumeClaim": "A request for storage; Kubernetes binds it to a "
                             "matching PersistentVolume.",
    "StorageClass": "A recipe for creating volumes on demand -- which driver, which "
                    "disk type, which reclaim policy.",
    "ResourceQuota": "A hard cap on what one namespace may consume in total.",
    "LimitRange": "Per-container defaults and min/max inside a namespace.",
    "ServiceAccount": "The identity a pod uses when it calls the API server.",
    "Role": "A set of permissions (verbs on resources) inside one namespace.",
    "RoleBinding": "Grants a Role to users, groups or ServiceAccounts in one "
                   "namespace.",
    "ClusterRole": "A set of permissions that is not tied to a namespace.",
    "ClusterRoleBinding": "Grants a ClusterRole across the whole cluster.",
    "HorizontalPodAutoscaler": "Adds and removes pod replicas to keep a metric near "
                               "its target.",
    "VerticalPodAutoscaler": "Recommends (or applies) better CPU/memory requests for "
                             "a workload.",
    "PodDisruptionBudget": "The minimum availability the cluster must respect during "
                           "drains and upgrades.",
    "CustomResourceDefinition": "Adds a new object kind to the Kubernetes API.",
    "CertificateSigningRequest": "A request for the cluster CA to issue a client "
                                 "certificate.",
    "Event": "A timestamped note from a controller about something that happened.",
}


def _detail(cluster: Cluster, obj: dict) -> str:
    """What this particular object does, read from its own spec."""
    kind = obj.get("kind")
    name = deep_get(obj, "metadata.name")
    spec = obj.get("spec") or {}

    if kind == "Pod":
        containers = deep_get(obj, "spec.containers", []) or []
        images = ", ".join(str(c.get("image")) for c in containers)
        node = deep_get(obj, "spec.nodeName") or "not scheduled yet"
        cpu, mem = 0.0, 0.0
        for container in containers:
            request = deep_get(container, "resources.requests", {}) or {}
            cpu += parse_cpu(request.get("cpu", 0))
            mem += parse_mem(request.get("memory", 0))
        sized = f", requests {fmt_cpu(cpu)}/{fmt_mem(mem)}" if cpu or mem else \
            ", no resource requests"
        return (f"Runs {images} on {node}{sized}. "
                f"Status {deep_get(obj, 'status.reason') or deep_get(obj, 'status.phase')}.")

    if kind in ("Deployment", "ReplicaSet", "StatefulSet"):
        replicas = deep_get(obj, "spec.replicas", 1)
        ready = deep_get(obj, "status.readyReplicas", 0)
        images = ", ".join(str(c.get("image")) for c in
                           deep_get(obj, "spec.template.spec.containers", []) or [])
        selector = deep_get(obj, "spec.selector.matchLabels", {}) or {}
        return (f"Keeps {replicas} pod(s) of {images} running ({ready} ready), "
                f"selected by {', '.join(f'{k}={v}' for k, v in selector.items())}.")

    if kind == "DaemonSet":
        return (f"One pod per node: {deep_get(obj, 'status.numberReady', 0)} ready of "
                f"{deep_get(obj, 'status.desiredNumberScheduled', 0)} eligible nodes.")

    if kind == "Job":
        return (f"Runs to completion: {deep_get(obj, 'status.succeeded', 0)}/"
                f"{deep_get(obj, 'spec.completions', 1)} succeeded, parallelism "
                f"{deep_get(obj, 'spec.parallelism', 1)}.")

    if kind == "CronJob":
        return (f"Creates a Job on schedule '{deep_get(obj, 'spec.schedule')}' "
                f"(concurrencyPolicy {deep_get(obj, 'spec.concurrencyPolicy', 'Allow')}).")

    if kind == "Service":
        ports = ", ".join(f"{p.get('port')}->{p.get('targetPort')}"
                          for p in deep_get(obj, "spec.ports", []) or [])
        selector = deep_get(obj, "spec.selector", {}) or {}
        endpoints = deep_get(obj, "status.endpoints", []) or []
        return (f"{deep_get(obj, 'spec.type', 'ClusterIP')} on "
                f"{deep_get(obj, 'spec.clusterIP', 'None')}, ports {ports or 'none'}, "
                f"selecting {', '.join(f'{k}={v}' for k, v in selector.items()) or 'nothing'}"
                f" -- {len(endpoints)} ready endpoint(s).")

    if kind == "Ingress":
        rules = []
        for rule in deep_get(obj, "spec.rules", []) or []:
            for path in deep_get(rule, "http.paths", []) or []:
                rules.append(f"{rule.get('host', '*')}{path.get('path', '/')} -> "
                             f"{deep_get(path, 'backend.service.name')}")
        return f"Routes {'; '.join(rules) or 'nothing'}."

    if kind == "NetworkPolicy":
        selector = deep_get(obj, "spec.podSelector.matchLabels", {}) or {}
        types = ", ".join(deep_get(obj, "spec.policyTypes", []) or ["Ingress"])
        return (f"Isolates pods matching "
                f"{', '.join(f'{k}={v}' for k, v in selector.items()) or 'everything'} "
                f"for {types}; only the listed peers may pass.")

    if kind in ("ConfigMap", "Secret"):
        keys = list((obj.get("data") or {}).keys()) + \
            list((obj.get("stringData") or {}).keys())
        return (f"Holds {len(keys)} key(s): {', '.join(keys) or 'none'}"
                + (f" (type {obj.get('type')})" if kind == "Secret" else "") + ".")

    if kind == "PersistentVolumeClaim":
        return (f"Asks for {deep_get(obj, 'spec.resources.requests.storage', '?')} "
                f"({','.join(deep_get(obj, 'spec.accessModes', []) or [])}) from class "
                f"{deep_get(obj, 'spec.storageClassName', 'default')} -- currently "
                f"{deep_get(obj, 'status.phase', 'Pending')}"
                + (f", bound to {deep_get(obj, 'status.volumeName')}"
                   if deep_get(obj, "status.volumeName") else "") + ".")

    if kind == "PersistentVolume":
        return (f"{deep_get(obj, 'spec.capacity.storage', '?')} of storage, reclaim "
                f"policy {deep_get(obj, 'spec.persistentVolumeReclaimPolicy', 'Retain')}, "
                f"phase {deep_get(obj, 'status.phase', 'Available')}.")

    if kind == "StorageClass":
        return (f"Provisions with {obj.get('provisioner')}, reclaim "
                f"{obj.get('reclaimPolicy', 'Delete')}, binding "
                f"{obj.get('volumeBindingMode', 'Immediate')}.")

    if kind == "HorizontalPodAutoscaler":
        ref = deep_get(obj, "spec.scaleTargetRef", {}) or {}
        return (f"Scales {ref.get('kind')}/{ref.get('name')} between "
                f"{deep_get(obj, 'spec.minReplicas', 1)} and "
                f"{deep_get(obj, 'spec.maxReplicas', 10)} replicas, targeting "
                f"{deep_get(obj, 'status.targetCPUUtilizationPercentage', 80)}% CPU "
                f"(currently {deep_get(obj, 'status.currentCPUUtilizationPercentage', 0)}%).")

    if kind in ("Role", "ClusterRole"):
        rules = obj.get("rules", []) or []
        summary = "; ".join(f"{','.join(r.get('verbs', []))} on "
                            f"{','.join(r.get('resources', []))}" for r in rules)
        return f"Grants: {summary or 'nothing'}."

    if kind in ("RoleBinding", "ClusterRoleBinding"):
        subjects = ", ".join(f"{s.get('kind')} {s.get('name')}"
                             for s in obj.get("subjects", []) or [])
        return (f"Gives {deep_get(obj, 'roleRef.kind')} "
                f"{deep_get(obj, 'roleRef.name')} to {subjects or 'nobody'}.")

    if kind == "ResourceQuota":
        hard = deep_get(obj, "spec.hard", {}) or {}
        return "Caps " + ", ".join(f"{k}={v}" for k, v in hard.items()) + "."

    if kind == "ServiceAccount":
        return f"Identity '{name}' that pods in this namespace can run as."

    if kind == "CertificateSigningRequest":
        return (f"Requests a client certificate for user "
                f"{deep_get(obj, 'spec.username')} in groups "
                f"{','.join(deep_get(obj, 'spec.groups', []) or [])} -- "
                f"{deep_get(obj, 'status.phase', 'Pending')}.")

    if kind == "Namespace":
        return f"Name scope '{name}'."

    if kind == "Node":
        usage = deep_get(obj, "status.usage", {}) or {}
        return (f"{deep_get(obj, 'status.role', 'worker')} running "
                f"{deep_get(obj, 'status.nodeInfo.kubeletVersion', '')}, "
                f"{usage.get('pods', 0)} pods, cpu {usage.get('cpuPct', 0)}%.")

    return ""


def _origin(cluster: Cluster, obj: dict) -> Dict[str, str]:
    """Who created this: you, or a controller acting on something you created."""
    owners = deep_get(obj, "metadata.ownerReferences", []) or []
    if owners:
        owner = owners[0]
        return {"by": "controller",
                "detail": f"created automatically by its owner "
                          f"{owner.get('kind')}/{owner.get('name')}"}
    command = annotation(obj, "lab.k8s/created-by")
    if command:
        return {"by": "you", "detail": command}
    return {"by": "cluster", "detail": "part of the starting cluster"}


def build(cluster: Cluster, namespace: Optional[str] = None,
          include_system: bool = False) -> List[dict]:
    """Every object in creation order, explained."""
    objects = []
    for obj in cluster.all_objects():
        ns = deep_get(obj, "metadata.namespace", "")
        if namespace not in (None, "", "*") and ns and ns != namespace:
            continue
        if not include_system and ns in ("kube-system", "kube-public",
                                         "kube-node-lease"):
            continue
        objects.append(obj)

    objects.sort(key=lambda o: (deep_get(o, "metadata.creationTick", 0),
                                deep_get(o, "metadata.creationOrder", 0),
                                str(deep_get(o, "metadata.name"))))

    out = []
    for index, obj in enumerate(objects, start=1):
        kind = str(obj.get("kind"))
        rk = resolve_kind(kind)
        topic = handbook.TOPICS.get(rk.topic) if rk else None
        origin = _origin(cluster, obj)
        out.append({
            "order": index,
            "tick": deep_get(obj, "metadata.creationTick", 0),
            "age": fmt_age(cluster.tick - deep_get(obj, "metadata.creationTick", 0)),
            "kind": kind,
            "name": deep_get(obj, "metadata.name"),
            "ns": deep_get(obj, "metadata.namespace", ""),
            "id": f"{kind}/{deep_get(obj, 'metadata.namespace', '-') or '-'}/"
                  f"{deep_get(obj, 'metadata.name')}",
            "purpose": PURPOSE.get(kind, ""),
            "detail": _detail(cluster, obj),
            "origin": origin["by"],
            "origin_detail": origin["detail"],
            "topic": rk.topic if rk else "",
            "topic_title": topic.title if topic else "",
            "page": topic.pages[0] if topic else None,
            "lab": topic.lab_number if topic else None,
        })
    return out


def to_text(cluster: Cluster, entries: List[dict]) -> str:
    lines = [f"# Object dictionary -- {cluster.name}",
             f"# {len(entries)} objects, in the order they were created",
             ""]
    for entry in entries:
        lines.append(f"{entry['order']:>3}. {entry['kind']}/{entry['name']}"
                     + (f"  (namespace {entry['ns']})" if entry["ns"] else ""))
        lines.append(f"     created: {entry['origin_detail']}  [{entry['age']} ago]")
        if entry["purpose"]:
            lines.append(f"     what it is: {entry['purpose']}")
        if entry["detail"]:
            lines.append(f"     what it does: {entry['detail']}")
        if entry["topic_title"]:
            lines.append(f"     handbook: {entry['topic_title']}"
                         + (f", page {entry['page']}" if entry["page"] else "")
                         + (f", lab {entry['lab']:02d}" if entry["lab"] else ""))
        lines.append("")
    return "\n".join(lines)
