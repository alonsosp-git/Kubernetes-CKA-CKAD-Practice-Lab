"""The simulated API server: admission, validation, RBAC and object storage.

Mirrors the request path drawn on handbook pages 34-38 and 49:

    request -> authentication -> authorization (RBAC) -> admission
            -> validation -> etcd (Cluster.objects)
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional

from .model import (Cluster, ResourceKind, deep_get, match_labels, parse_cpu,
                    parse_mem, resolve_kind, NAME_RE)


class ApiError(Exception):
    """Raised for anything the API server would reject."""


class Forbidden(ApiError):
    pass


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------
REQUIRED_SPEC: Dict[str, List[str]] = {
    "Pod": ["containers"],
    "Deployment": ["selector", "template"],
    "ReplicaSet": ["selector", "template"],
    "StatefulSet": ["selector", "template", "serviceName"],
    "DaemonSet": ["selector", "template"],
    "Job": ["template"],
    "CronJob": ["schedule", "jobTemplate"],
    "Service": [],
    "Ingress": ["rules"],
    "PersistentVolume": ["capacity", "accessModes"],
    "PersistentVolumeClaim": ["accessModes", "resources"],
    "HorizontalPodAutoscaler": ["scaleTargetRef", "maxReplicas"],
}

VALID_SERVICE_TYPES = ("ClusterIP", "NodePort", "LoadBalancer", "ExternalName", "Headless")


def _validate_container(container: dict, path: str) -> None:
    if not container.get("name"):
        raise ApiError(f"{path}: container name is required")
    if not container.get("image"):
        raise ApiError(f"{path}.{container['name']}: image is required")
    for section in ("requests", "limits"):
        res = deep_get(container, f"resources.{section}", {}) or {}
        if "cpu" in res:
            parse_cpu(res["cpu"])
        if "memory" in res:
            parse_mem(res["memory"])


def validate(obj: dict, cluster: Cluster) -> None:
    kind = obj.get("kind")
    rk = resolve_kind(kind or "")
    if rk is None:
        raise ApiError(f'no matches for kind "{kind}" in version '
                       f'"{obj.get("apiVersion", "v1")}"')
    name = deep_get(obj, "metadata.name")
    if not name:
        raise ApiError(f"{rk.kind}: metadata.name is required")
    if not NAME_RE.match(str(name)):
        raise ApiError(
            f'"{name}" is invalid: a lowercase RFC 1123 subdomain must consist of '
            "lower case alphanumeric characters, '-' or '.'")
    if len(str(name)) > 253:
        raise ApiError(f'"{name}" is invalid: must be no more than 253 characters')

    spec = obj.get("spec") or {}
    for required in REQUIRED_SPEC.get(rk.kind, []):
        if required not in spec and not (rk.kind == "Pod" and required == "containers"):
            raise ApiError(f"{rk.kind}/{name}: spec.{required} is required")

    if rk.kind == "Pod":
        containers = spec.get("containers") or []
        if not containers:
            raise ApiError(f"Pod/{name}: spec.containers must have at least one entry")
        for c in containers:
            _validate_container(c, "spec.containers")
        for c in spec.get("initContainers") or []:
            _validate_container(c, "spec.initContainers")

    if rk.kind in ("Deployment", "ReplicaSet", "StatefulSet", "DaemonSet"):
        selector = spec.get("selector") or {}
        labels = deep_get(spec, "template.metadata.labels", {}) or {}
        if not match_labels(labels, selector):
            raise ApiError(
                f"{rk.kind}/{name}: `selector` does not match template `labels` "
                f"(selector={selector.get('matchLabels', selector)}, labels={labels}) "
                "-- this is the #1 beginner mistake, see handbook page 6")
        for c in deep_get(spec, "template.spec.containers", []) or []:
            _validate_container(c, "spec.template.spec.containers")

    if rk.kind == "Service":
        stype = spec.get("type", "ClusterIP")
        if stype not in VALID_SERVICE_TYPES:
            raise ApiError(f"Service/{name}: unsupported type {stype!r}; "
                           f"expected one of {', '.join(VALID_SERVICE_TYPES)}")
        for port in spec.get("ports") or []:
            if "port" not in port:
                raise ApiError(f"Service/{name}: spec.ports[].port is required")
            if stype == "NodePort" and "nodePort" in port:
                np = int(port["nodePort"])
                if not 30000 <= np <= 32767:
                    raise ApiError(f"Service/{name}: nodePort {np} out of range "
                                   "30000-32767")

    if rk.kind == "HorizontalPodAutoscaler":
        mini = spec.get("minReplicas", 1)
        maxi = spec.get("maxReplicas")
        if maxi is not None and int(mini) > int(maxi):
            raise ApiError(f"HPA/{name}: minReplicas must be <= maxReplicas")

    if rk.namespaced:
        ns = deep_get(obj, "metadata.namespace") or "default"
        if not cluster.get("Namespace", "", ns):
            raise ApiError(f'namespaces "{ns}" not found -- create it first with '
                           f"`kubectl create namespace {ns}`")


# --------------------------------------------------------------------------
# admission (defaulting + mutation + quota)
# --------------------------------------------------------------------------
def _default_metadata(obj: dict, cluster: Cluster, rk: ResourceKind) -> None:
    meta = obj.setdefault("metadata", {})
    if rk.namespaced:
        meta.setdefault("namespace", cluster.current_namespace)
    else:
        meta.pop("namespace", None)
    meta.setdefault("labels", {})
    meta.setdefault("annotations", {})
    meta.setdefault("uid", cluster.next_uid())
    meta.setdefault("creationTick", cluster.tick)
    obj.setdefault("apiVersion", rk.api_version)
    obj.setdefault("status", {})


def _mutate(obj: dict, cluster: Cluster, rk: ResourceKind) -> None:
    """Mutating admission webhooks: fill in the defaults a real cluster adds."""
    spec = obj.setdefault("spec", {})
    if rk.kind == "Pod":
        spec.setdefault("restartPolicy", "Always")
        spec.setdefault("serviceAccountName", "default")
        spec.setdefault("terminationGracePeriodSeconds", 30)
        for container in spec.get("containers", []):
            image = str(container.get("image", ""))
            if ":" not in image.split("/")[-1]:
                container["image"] = image + ":latest"
            container.setdefault("imagePullPolicy",
                                 "Always" if container["image"].endswith(":latest")
                                 else "IfNotPresent")
        obj.setdefault("status", {}).setdefault("phase", "Pending")
    elif rk.kind in ("Deployment", "ReplicaSet", "StatefulSet"):
        spec.setdefault("replicas", 1)
        if rk.kind == "Deployment":
            spec.setdefault("strategy", {"type": "RollingUpdate",
                                         "rollingUpdate": {"maxSurge": "25%",
                                                           "maxUnavailable": "25%"}})
    elif rk.kind == "Service":
        spec.setdefault("type", "ClusterIP")
        if spec.get("clusterIP") != "None" and not obj["spec"].get("clusterIP"):
            spec["clusterIP"] = _alloc_cluster_ip(cluster)
        for port in spec.get("ports", []):
            port.setdefault("protocol", "TCP")
            port.setdefault("targetPort", port.get("port"))
            if spec.get("type") == "NodePort":
                port.setdefault("nodePort", _alloc_node_port(cluster))
        if spec.get("type") == "LoadBalancer":
            obj.setdefault("status", {})["loadBalancer"] = {
                "ingress": [{"ip": f"203.0.113.{(len(cluster.list('Service')) % 250) + 2}"}]}
    elif rk.kind == "PersistentVolumeClaim":
        spec.setdefault("accessModes", ["ReadWriteOnce"])
        obj.setdefault("status", {}).setdefault("phase", "Pending")
    elif rk.kind == "PersistentVolume":
        spec.setdefault("persistentVolumeReclaimPolicy", "Retain")
        obj.setdefault("status", {}).setdefault("phase", "Available")
    elif rk.kind == "Namespace":
        obj.setdefault("status", {})["phase"] = "Active"
    elif rk.kind == "HorizontalPodAutoscaler":
        spec.setdefault("minReplicas", 1)


def _alloc_cluster_ip(cluster: Cluster) -> str:
    used = {deep_get(s, "spec.clusterIP") for s in cluster.list("Service")}
    for i in range(1, 250):
        candidate = f"10.96.0.{i}"
        if candidate not in used:
            return candidate
    return "10.96.0.254"


def _alloc_node_port(cluster: Cluster) -> int:
    used = set()
    for svc in cluster.list("Service"):
        for port in deep_get(svc, "spec.ports", []) or []:
            if port.get("nodePort"):
                used.add(int(port["nodePort"]))
    for candidate in range(30000, 32768):
        if candidate not in used:
            return candidate
    raise ApiError("no free nodePort available")


def check_quota(obj: dict, cluster: Cluster) -> None:
    """ResourceQuota admission (handbook page 8)."""
    if obj.get("kind") not in ("Pod", "Deployment", "ReplicaSet", "StatefulSet"):
        return
    ns = deep_get(obj, "metadata.namespace") or "default"
    quotas = cluster.list("ResourceQuota", ns)
    if not quotas:
        return
    pods = [p for p in cluster.list("Pod", ns)]
    for quota in quotas:
        hard = deep_get(quota, "spec.hard", {}) or {}
        limit_pods = hard.get("pods")
        if limit_pods is not None and obj.get("kind") == "Pod":
            if len(pods) >= int(limit_pods):
                raise Forbidden(
                    f'exceeded quota: {deep_get(quota, "metadata.name")}, '
                    f"requested: pods=1, used: pods={len(pods)}, "
                    f"limited: pods={limit_pods}")


# --------------------------------------------------------------------------
# RBAC (handbook pages 34 & 37)
# --------------------------------------------------------------------------
def _rules_for(cluster: Cluster, user: str, namespace: str) -> List[dict]:
    rules: List[dict] = []
    subject_matches = lambda s: (  # noqa: E731
        (s.get("kind") == "User" and s.get("name") == user) or
        (s.get("kind") == "Group" and s.get("name") in ("system:authenticated",)) or
        (s.get("kind") == "ServiceAccount" and
         f'system:serviceaccount:{s.get("namespace", namespace)}:{s.get("name")}' == user))

    for binding in cluster.list("ClusterRoleBinding"):
        if any(subject_matches(s) for s in deep_get(binding, "subjects", []) or []):
            role_name = deep_get(binding, "roleRef.name")
            role = cluster.get("ClusterRole", "", role_name)
            if role:
                rules.extend(deep_get(role, "rules", []) or [])
    for binding in cluster.list("RoleBinding", namespace):
        if any(subject_matches(s) for s in deep_get(binding, "subjects", []) or []):
            ref = deep_get(binding, "roleRef", {}) or {}
            role = (cluster.get("ClusterRole", "", ref.get("name"))
                    if ref.get("kind") == "ClusterRole"
                    else cluster.get("Role", namespace, ref.get("name")))
            if role:
                rules.extend(deep_get(role, "rules", []) or [])
    return rules


def can_i(cluster: Cluster, verb: str, resource: str, namespace: str,
          user: Optional[str] = None) -> bool:
    user = user or cluster.current_user
    if not cluster.rbac_enforced or user in ("admin", "kubernetes-admin"):
        return True
    rk = resolve_kind(resource)
    plural = rk.plural if rk else resource
    for rule in _rules_for(cluster, user, namespace):
        verbs = [v.lower() for v in rule.get("verbs", [])]
        resources = rule.get("resources", [])
        if ("*" in verbs or verb.lower() in verbs) and \
                ("*" in resources or plural in resources):
            return True
    return False


def authorize(cluster: Cluster, verb: str, resource: str, namespace: str) -> None:
    if not can_i(cluster, verb, resource, namespace):
        raise Forbidden(
            f'User "{cluster.current_user}" cannot {verb} resource "{resource}" '
            f'in API group "" in the namespace "{namespace}" '
            "(RBAC denies by default -- create a Role + RoleBinding, handbook page 34)")


# --------------------------------------------------------------------------
# request path
# --------------------------------------------------------------------------
def apply(cluster: Cluster, obj: dict, dry_run: bool = False) -> str:
    """Create-or-update, returning the kubectl-style result line."""
    obj = copy.deepcopy(obj)
    kind = obj.get("kind")
    rk = resolve_kind(kind or "")
    if rk is None:
        raise ApiError(f'no matches for kind "{kind}"')

    _default_metadata(obj, cluster, rk)
    ns = deep_get(obj, "metadata.namespace") or ""
    authorize(cluster, "create", rk.plural, ns or "default")
    _mutate(obj, cluster, rk)
    validate(obj, cluster)
    check_quota(obj, cluster)

    name = deep_get(obj, "metadata.name")
    existing = cluster.get(rk.kind, ns, name)
    label = f"{rk.plural[:-1] if rk.plural.endswith('s') else rk.plural}/{name}"
    label = f"{rk.kind.lower()}/{name}"
    if dry_run:
        return f"{label} created (server dry run)"
    if existing:
        obj["metadata"]["uid"] = deep_get(existing, "metadata.uid")
        obj["metadata"]["creationTick"] = deep_get(existing, "metadata.creationTick",
                                                   cluster.tick)
        old_spec = existing.get("spec")
        # keep controller-managed status
        obj["status"] = existing.get("status", {}) or obj.get("status", {})
        cluster.put(obj)
        if old_spec != obj.get("spec"):
            obj.setdefault("metadata", {}).setdefault("annotations", {})[
                "lab.k8s/last-change-tick"] = cluster.tick
            cluster.record("Normal", "Updated", f"Updated {rk.kind} {name}",
                           label, ns or "default")
            return f"{label} configured"
        return f"{label} unchanged"
    if getattr(cluster, "current_command", ""):
        obj["metadata"].setdefault("annotations", {})["lab.k8s/created-by"] = \
            cluster.current_command
    obj["metadata"]["creationOrder"] = cluster.uid_counter
    cluster.put(obj)
    cluster.record("Normal", "Created", f"Created {rk.kind} {name}", label,
                   ns or "default")
    return f"{label} created"


def delete(cluster: Cluster, kind: str, namespace: str, name: str) -> str:
    rk = resolve_kind(kind)
    if rk is None:
        raise ApiError(f'the server doesn\'t have a resource type "{kind}"')
    ns = namespace if rk.namespaced else ""
    authorize(cluster, "delete", rk.plural, ns or "default")
    obj = cluster.get(rk.kind, ns, name)
    if obj is None:
        raise ApiError(f'{rk.plural} "{name}" not found')
    cluster.delete(rk.kind, ns, name)
    _cascade_delete(cluster, rk.kind, ns, name, obj)
    cluster.record("Normal", "Deleted", f"Deleted {rk.kind} {name}",
                   f"{rk.kind.lower()}/{name}", ns or "default")
    return f'{rk.kind.lower()}"{name}" deleted'.replace('"', ' "')


def _cascade_delete(cluster: Cluster, kind: str, ns: str, name: str,
                    obj: dict) -> None:
    """Garbage collection via ownerReferences."""
    changed = True
    while changed:
        changed = False
        for key, candidate in list(cluster.objects.items()):
            for owner in deep_get(candidate, "metadata.ownerReferences", []) or []:
                if owner.get("kind") == kind and owner.get("name") == name and \
                        key.namespace == ns:
                    cluster.objects.pop(key, None)
                    _cascade_delete(cluster, key.kind, key.namespace, key.name,
                                    candidate)
                    changed = True
                    break
    if kind == "Namespace":
        for key in list(cluster.objects):
            if key.namespace == name:
                cluster.objects.pop(key, None)
    if kind == "PersistentVolumeClaim":
        for pv in cluster.list("PersistentVolume"):
            ref = deep_get(pv, "spec.claimRef", {}) or {}
            if ref.get("name") == name and ref.get("namespace") == ns:
                policy = deep_get(pv, "spec.persistentVolumeReclaimPolicy", "Retain")
                if policy == "Delete":
                    cluster.delete("PersistentVolume", "", deep_get(pv, "metadata.name"))
                else:
                    pv["status"]["phase"] = "Released"
                    pv["spec"].pop("claimRef", None)


def scale(cluster: Cluster, kind: str, namespace: str, name: str,
          replicas: int) -> str:
    rk = resolve_kind(kind)
    if rk is None or rk.kind not in ("Deployment", "ReplicaSet", "StatefulSet"):
        raise ApiError(f"cannot scale resource of kind {kind}")
    authorize(cluster, "update", rk.plural, namespace)
    obj = cluster.get(rk.kind, namespace, name)
    if obj is None:
        raise ApiError(f'{rk.plural} "{name}" not found')
    if replicas < 0:
        raise ApiError("replicas must be >= 0")
    obj.setdefault("spec", {})["replicas"] = replicas
    cluster.record("Normal", "Scaled", f"Scaled {rk.kind} {name} to {replicas}",
                   f"{rk.kind.lower()}/{name}", namespace)
    return f"{rk.kind.lower()}/{name} scaled"
