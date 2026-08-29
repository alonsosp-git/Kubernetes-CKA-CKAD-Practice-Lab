"""The controller manager (handbook page 41).

Each controller is a small function that compares *desired* state (spec) with
*observed* state (the objects that exist) and takes one step to close the gap.
``reconcile()`` runs them all once per simulation tick, which is exactly the
control loop the handbook draws.

Controllers implemented:
    Deployment -> ReplicaSet -> Pod    (with rolling updates + revision history)
    StatefulSet (ordinal names, per-replica PVCs)
    DaemonSet   (one pod per eligible node)
    Job / CronJob
    Endpoints   (Service -> matching pod IPs)
    PVC binding + dynamic provisioning via StorageClass
    HPA         (scales on synthetic CPU load)
    Node lifecycle, pod lifecycle, probes, image pull, chaos injection
"""
from __future__ import annotations

import copy
from typing import Dict, List, Optional

from .model import (Cluster, annotation, deep_get, fmt_cpu, label, match_labels,
                    parse_cpu, parse_mem, rand_suffix, short_hash)
from .scheduler import node_allocatable, node_usage, pod_requests, schedule_pod

# images that will never pull -- used to practise ImagePullBackOff
BAD_IMAGE_MARKERS = ("nonexistent", "doesnotexist", "does-not-exist", "badimage",
                     "bad-image", "invalid", "typo", ":notag", "nosuchimage")

REVISION_KEY = "deployment.kubernetes.io/revision"

READY_DELAY = 3          # ticks from ContainerCreating -> Running
INIT_DELAY = 2           # ticks per init container
JOB_DURATION = 5         # ticks for a Job pod to complete


# --------------------------------------------------------------------------
# pod construction
# --------------------------------------------------------------------------
def _pod_from_template(cluster: Cluster, owner: dict, template: dict, name: str,
                       namespace: str, owner_kind: str) -> dict:
    meta = copy.deepcopy(template.get("metadata", {}) or {})
    spec = copy.deepcopy(template.get("spec", {}) or {})
    meta["name"] = name
    meta["namespace"] = namespace
    meta.setdefault("labels", {})
    meta.setdefault("annotations", {})
    meta["uid"] = cluster.next_uid()
    meta["creationTick"] = cluster.tick
    meta["ownerReferences"] = [{
        "apiVersion": owner.get("apiVersion", "apps/v1"),
        "kind": owner_kind,
        "name": deep_get(owner, "metadata.name"),
        "uid": deep_get(owner, "metadata.uid"),
        "controller": True,
    }]
    spec.setdefault("restartPolicy", "Always" if owner_kind not in ("Job", "CronJob")
                    else "OnFailure")
    spec.setdefault("serviceAccountName", "default")
    for container in spec.get("containers", []):
        image = str(container.get("image", ""))
        if ":" not in image.split("/")[-1]:
            container["image"] = image + ":latest"
    pod = {"apiVersion": "v1", "kind": "Pod", "metadata": meta, "spec": spec,
           "status": {"phase": "Pending", "restartCount": 0}}
    return pod


def _pod_ip(cluster: Cluster) -> str:
    used = {deep_get(p, "status.podIP") for p in cluster.list("Pod")}
    for third in range(0, 20):
        for fourth in range(2, 255):
            candidate = f"10.244.{third}.{fourth}"
            if candidate not in used:
                return candidate
    return "10.244.99.99"


def _owned_pods(cluster: Cluster, owner_kind: str, name: str, ns: str) -> List[dict]:
    out = []
    for pod in cluster.list("Pod", ns):
        for ref in deep_get(pod, "metadata.ownerReferences", []) or []:
            if ref.get("kind") == owner_kind and ref.get("name") == name:
                out.append(pod)
                break
    return out


def _alive(pod: dict) -> bool:
    return deep_get(pod, "status.phase") not in ("Succeeded", "Failed")


# --------------------------------------------------------------------------
# ReplicaSet controller
# --------------------------------------------------------------------------
def reconcile_replicaset(cluster: Cluster, rs: dict) -> None:
    name = deep_get(rs, "metadata.name")
    ns = deep_get(rs, "metadata.namespace", "default")
    desired = int(deep_get(rs, "spec.replicas", 1) or 0)
    pods = [p for p in _owned_pods(cluster, "ReplicaSet", name, ns) if _alive(p)]
    template = deep_get(rs, "spec.template", {}) or {}

    while len(pods) < desired:
        index = len(pods)
        pod_name = f"{name}-{rand_suffix(f'{name}-{cluster.uid_counter}-{index}')}"
        pod = _pod_from_template(cluster, rs, template, pod_name, ns, "ReplicaSet")
        pod["metadata"]["labels"].update(deep_get(rs, "spec.selector.matchLabels", {}) or {})
        cluster.put(pod)
        cluster.record("Normal", "SuccessfulCreate",
                       f"Created pod: {pod_name}", f"replicaset/{name}", ns)
        pods.append(pod)

    while len(pods) > desired:
        victim = sorted(pods, key=lambda p: (
            0 if deep_get(p, "status.phase") != "Running" else 1,
            -deep_get(p, "metadata.creationTick", 0)))[0]
        pods.remove(victim)
        cluster.delete("Pod", ns, deep_get(victim, "metadata.name"))
        cluster.record("Normal", "SuccessfulDelete",
                       f"Deleted pod: {deep_get(victim, 'metadata.name')}",
                       f"replicaset/{name}", ns)

    ready = sum(1 for p in pods if deep_get(p, "status.ready"))
    rs.setdefault("status", {}).update({
        "replicas": len(pods), "readyReplicas": ready, "availableReplicas": ready,
        "fullyLabeledReplicas": len(pods)})


# --------------------------------------------------------------------------
# Deployment controller (with rolling update)
# --------------------------------------------------------------------------
def _template_hash(template: dict) -> str:
    return short_hash(repr(sorted(_flatten(template))))


def _flatten(obj, prefix=""):
    items = []
    if isinstance(obj, dict):
        for key, val in sorted(obj.items()):
            if key in ("creationTick", "uid"):
                continue
            items.extend(_flatten(val, f"{prefix}.{key}"))
    elif isinstance(obj, list):
        for i, val in enumerate(obj):
            items.extend(_flatten(val, f"{prefix}[{i}]"))
    else:
        items.append((prefix, str(obj)))
    return items


def reconcile_deployment(cluster: Cluster, dep: dict) -> None:
    name = deep_get(dep, "metadata.name")
    ns = deep_get(dep, "metadata.namespace", "default")
    desired = int(deep_get(dep, "spec.replicas", 1) or 0)
    template = deep_get(dep, "spec.template", {}) or {}
    pod_hash = _template_hash(template)

    replicasets = [rs for rs in _owned_pods_rs(cluster, name, ns)]
    current = next((rs for rs in replicasets
                    if label(rs, "pod-template-hash") == pod_hash),
                   None)
    if current is None:
        rs_name = f"{name}-{pod_hash[:9]}"
        labels = dict(deep_get(dep, "spec.selector.matchLabels", {}) or {})
        labels["pod-template-hash"] = pod_hash
        rs_template = copy.deepcopy(template)
        rs_template.setdefault("metadata", {}).setdefault("labels", {})[
            "pod-template-hash"] = pod_hash
        revision = 1 + max([int(annotation(rs, REVISION_KEY, 0) or 0)
                            for rs in replicasets] or [0])
        current = {
            "apiVersion": "apps/v1", "kind": "ReplicaSet",
            "metadata": {"name": rs_name, "namespace": ns, "labels": labels,
                         "annotations": {REVISION_KEY: revision},
                         "uid": cluster.next_uid(), "creationTick": cluster.tick,
                         "ownerReferences": [{"apiVersion": "apps/v1",
                                              "kind": "Deployment", "name": name,
                                              "uid": deep_get(dep, "metadata.uid"),
                                              "controller": True}]},
            "spec": {"replicas": 0,
                     "selector": {"matchLabels": labels},
                     "template": rs_template},
            "status": {},
        }
        cluster.put(current)
        cluster.record("Normal", "ScalingReplicaSet",
                       f"Scaled up replica set {rs_name} to {desired}",
                       f"deployment/{name}", ns)
        replicasets.append(current)

    strategy = deep_get(dep, "spec.strategy.type", "RollingUpdate")
    old_sets = [rs for rs in replicasets if rs is not current]
    old_total = sum(int(deep_get(rs, "spec.replicas", 0) or 0) for rs in old_sets)

    if strategy == "Recreate" and old_total:
        for rs in old_sets:
            rs["spec"]["replicas"] = 0
        current["spec"]["replicas"] = 0
    else:
        max_surge = _pct(deep_get(dep, "spec.strategy.rollingUpdate.maxSurge", "25%"),
                         desired, default=1)
        ready_now = sum(1 for p in _owned_pods(cluster, "ReplicaSet",
                                               deep_get(current, "metadata.name"), ns)
                        if deep_get(p, "status.ready"))
        target = min(desired, ready_now + max(1, max_surge))
        if old_total == 0:
            target = desired
        current["spec"]["replicas"] = max(0, target)
        # scale old sets down as new pods become ready
        surplus = sum(int(deep_get(rs, "spec.replicas", 0) or 0)
                      for rs in replicasets) - desired
        for rs in sorted(old_sets, key=lambda r: deep_get(r, "metadata.creationTick", 0)):
            if surplus <= 0:
                break
            have = int(deep_get(rs, "spec.replicas", 0) or 0)
            take = min(have, surplus)
            rs["spec"]["replicas"] = have - take
            surplus -= take
        if ready_now >= desired:
            for rs in old_sets:
                rs["spec"]["replicas"] = 0

    for rs in old_sets:
        if int(deep_get(rs, "spec.replicas", 0) or 0) == 0 and \
                not _owned_pods(cluster, "ReplicaSet", deep_get(rs, "metadata.name"), ns):
            history = int(deep_get(dep, "spec.revisionHistoryLimit", 10) or 10)
            kept = [r for r in old_sets if int(deep_get(r, "spec.replicas", 0) or 0) == 0]
            if len(kept) > history:
                cluster.delete("ReplicaSet", ns, deep_get(rs, "metadata.name"))

    pods = [p for rs in replicasets
            for p in _owned_pods(cluster, "ReplicaSet", deep_get(rs, "metadata.name"), ns)]
    ready = sum(1 for p in pods if deep_get(p, "status.ready"))
    updated = sum(1 for p in _owned_pods(cluster, "ReplicaSet",
                                         deep_get(current, "metadata.name"), ns))
    dep.setdefault("status", {}).update({
        "replicas": len(pods), "readyReplicas": ready, "availableReplicas": ready,
        "updatedReplicas": updated,
        "conditions": [{"type": "Available",
                        "status": "True" if ready >= max(1, desired) else "False"},
                       {"type": "Progressing",
                        "status": "True" if ready == desired else "False"}]})


def _owned_pods_rs(cluster: Cluster, dep_name: str, ns: str) -> List[dict]:
    out = []
    for rs in cluster.list("ReplicaSet", ns):
        for ref in deep_get(rs, "metadata.ownerReferences", []) or []:
            if ref.get("kind") == "Deployment" and ref.get("name") == dep_name:
                out.append(rs)
                break
    return sorted(out, key=lambda r: deep_get(r, "metadata.creationTick", 0))


def _pct(value, total: int, default: int = 1) -> int:
    if value is None:
        return default
    text = str(value)
    if text.endswith("%"):
        return max(default, int(total * int(text[:-1]) / 100))
    try:
        return int(text)
    except ValueError:
        return default


# --------------------------------------------------------------------------
# StatefulSet / DaemonSet / Job / CronJob
# --------------------------------------------------------------------------
def reconcile_statefulset(cluster: Cluster, sts: dict) -> None:
    name = deep_get(sts, "metadata.name")
    ns = deep_get(sts, "metadata.namespace", "default")
    desired = int(deep_get(sts, "spec.replicas", 1) or 0)
    template = deep_get(sts, "spec.template", {}) or {}
    existing = {deep_get(p, "metadata.name"): p
                for p in _owned_pods(cluster, "StatefulSet", name, ns) if _alive(p)}

    for index in range(desired):
        pod_name = f"{name}-{index}"
        if pod_name in existing:
            continue
        # ordered startup: only create N if N-1 is ready
        if index > 0:
            prev = existing.get(f"{name}-{index - 1}")
            if not prev or not deep_get(prev, "status.ready"):
                break
        pod = _pod_from_template(cluster, sts, template, pod_name, ns, "StatefulSet")
        pod["metadata"]["labels"].update(
            deep_get(sts, "spec.selector.matchLabels", {}) or {})
        pod["spec"]["hostname"] = pod_name
        pod["spec"]["subdomain"] = deep_get(sts, "spec.serviceName", name)
        for claim in deep_get(sts, "spec.volumeClaimTemplates", []) or []:
            claim_name = f"{deep_get(claim, 'metadata.name')}-{pod_name}"
            if not cluster.get("PersistentVolumeClaim", ns, claim_name):
                pvc = copy.deepcopy(claim)
                pvc.update({"apiVersion": "v1", "kind": "PersistentVolumeClaim"})
                pvc.setdefault("metadata", {})
                pvc["metadata"].update({"name": claim_name, "namespace": ns,
                                        "uid": cluster.next_uid(),
                                        "creationTick": cluster.tick})
                pvc["metadata"].setdefault("labels", {}).update(
                    deep_get(sts, "spec.selector.matchLabels", {}) or {})
                pvc.setdefault("status", {})["phase"] = "Pending"
                cluster.put(pvc)
            pod["spec"].setdefault("volumes", []).append(
                {"name": deep_get(claim, "metadata.name"),
                 "persistentVolumeClaim": {"claimName": claim_name}})
        cluster.put(pod)
        cluster.record("Normal", "SuccessfulCreate",
                       f"create Pod {pod_name} in StatefulSet {name} successful",
                       f"statefulset/{name}", ns)
        existing[pod_name] = pod

    for pod_name, pod in list(existing.items()):
        index = int(pod_name.rsplit("-", 1)[-1]) if pod_name.rsplit("-", 1)[-1].isdigit() \
            else 0
        if index >= desired:
            cluster.delete("Pod", ns, pod_name)
            existing.pop(pod_name)

    ready = sum(1 for p in existing.values() if deep_get(p, "status.ready"))
    sts.setdefault("status", {}).update({"replicas": len(existing),
                                         "readyReplicas": ready,
                                         "currentReplicas": len(existing)})


def reconcile_daemonset(cluster: Cluster, ds: dict) -> None:
    name = deep_get(ds, "metadata.name")
    ns = deep_get(ds, "metadata.namespace", "default")
    template = deep_get(ds, "spec.template", {}) or {}
    pods = {deep_get(p, "spec.nodeName"): p
            for p in _owned_pods(cluster, "DaemonSet", name, ns) if _alive(p)}
    eligible = []
    for node in cluster.list("Node"):
        node_name = deep_get(node, "metadata.name")
        probe = copy.deepcopy(template.get("spec", {}))
        probe_pod = {"spec": probe}
        blocked = False
        for taint in deep_get(node, "spec.taints", []) or []:
            if taint.get("effect") == "PreferNoSchedule":
                continue
            from .scheduler import _tolerates
            if not _tolerates(probe_pod, taint):
                blocked = True
                break
        selector = deep_get(template, "spec.nodeSelector", {}) or {}
        labels = deep_get(node, "metadata.labels", {}) or {}
        if selector and not all(str(labels.get(k)) == str(v)
                                for k, v in selector.items()):
            blocked = True
        if not blocked:
            eligible.append(node_name)

    for node_name in eligible:
        if node_name in pods:
            continue
        pod_name = f"{name}-{rand_suffix(node_name + name)}"
        pod = _pod_from_template(cluster, ds, template, pod_name, ns, "DaemonSet")
        pod["metadata"]["labels"].update(deep_get(ds, "spec.selector.matchLabels", {}) or {})
        pod["spec"]["nodeName"] = node_name
        cluster.put(pod)
        cluster.record("Normal", "SuccessfulCreate", f"Created pod: {pod_name}",
                       f"daemonset/{name}", ns)
        pods[node_name] = pod

    for node_name, pod in list(pods.items()):
        if node_name not in eligible:
            cluster.delete("Pod", ns, deep_get(pod, "metadata.name"))
            pods.pop(node_name)

    ready = sum(1 for p in pods.values() if deep_get(p, "status.ready"))
    ds.setdefault("status", {}).update({
        "desiredNumberScheduled": len(eligible), "currentNumberScheduled": len(pods),
        "numberReady": ready, "numberAvailable": ready, "updatedNumberScheduled": len(pods)})


def reconcile_job(cluster: Cluster, job: dict) -> None:
    name = deep_get(job, "metadata.name")
    ns = deep_get(job, "metadata.namespace", "default")
    completions = int(deep_get(job, "spec.completions", 1) or 1)
    parallelism = int(deep_get(job, "spec.parallelism", 1) or 1)
    template = deep_get(job, "spec.template", {}) or {}
    pods = _owned_pods(cluster, "Job", name, ns)
    succeeded = [p for p in pods if deep_get(p, "status.phase") == "Succeeded"]
    failed = [p for p in pods if deep_get(p, "status.phase") == "Failed"]
    active = [p for p in pods if _alive(p)]

    if len(succeeded) < completions:
        want = min(parallelism, completions - len(succeeded)) - len(active)
        for _ in range(max(0, want)):
            pod_name = f"{name}-{rand_suffix(name + str(cluster.uid_counter))}"
            pod = _pod_from_template(cluster, job, template, pod_name, ns, "Job")
            pod["metadata"]["labels"]["job-name"] = name
            pod["status"]["jobPod"] = True
            cluster.put(pod)
            cluster.record("Normal", "SuccessfulCreate", f"Created pod: {pod_name}",
                           f"job/{name}", ns)
    job.setdefault("status", {}).update({
        "succeeded": len(succeeded), "failed": len(failed), "active": len(active)})
    if len(succeeded) >= completions and "completionTick" not in job["status"]:
        job["status"]["completionTick"] = cluster.tick
        job["status"]["conditions"] = [{"type": "Complete", "status": "True"}]
        cluster.record("Normal", "Completed", f"Job {name} completed",
                       f"job/{name}", ns)


_CRON_STEPS = {"* * * * *": 60, "*/1 * * * *": 60, "*/5 * * * *": 300,
               "0 * * * *": 3600, "0 0 * * *": 86400}


def reconcile_cronjob(cluster: Cluster, cj: dict) -> None:
    name = deep_get(cj, "metadata.name")
    ns = deep_get(cj, "metadata.namespace", "default")
    schedule = str(deep_get(cj, "spec.schedule", "*/1 * * * *"))
    period = _CRON_STEPS.get(schedule, 60)
    if deep_get(cj, "spec.suspend", False):
        return
    last = deep_get(cj, "status.lastScheduleTick", None)
    if last is None or cluster.tick - last >= period:
        cj.setdefault("status", {})["lastScheduleTick"] = cluster.tick
        job_name = f"{name}-{cluster.tick:d}"
        job_spec = copy.deepcopy(deep_get(cj, "spec.jobTemplate.spec", {}) or {})
        job = {"apiVersion": "batch/v1", "kind": "Job",
               "metadata": {"name": job_name, "namespace": ns,
                            "labels": {"cronjob": name},
                            "uid": cluster.next_uid(), "creationTick": cluster.tick,
                            "ownerReferences": [{"apiVersion": "batch/v1",
                                                 "kind": "CronJob", "name": name,
                                                 "uid": deep_get(cj, "metadata.uid"),
                                                 "controller": True}]},
               "spec": job_spec, "status": {}}
        cluster.put(job)
        cluster.record("Normal", "SuccessfulCreate", f"Created job {job_name}",
                       f"cronjob/{name}", ns)
    jobs = _owned_pods_generic(cluster, "CronJob", name, ns, "Job")
    limit = int(deep_get(cj, "spec.successfulJobsHistoryLimit", 3) or 3)
    finished = [j for j in jobs if deep_get(j, "status.succeeded", 0)]
    for job in sorted(finished, key=lambda j: deep_get(j, "metadata.creationTick", 0))[:-limit]:
        cluster.delete("Job", ns, deep_get(job, "metadata.name"))
    cj.setdefault("status", {})["active"] = len([j for j in jobs
                                                 if deep_get(j, "status.active", 0)])


def _owned_pods_generic(cluster: Cluster, owner_kind: str, owner_name: str,
                        ns: str, child_kind: str) -> List[dict]:
    out = []
    for obj in cluster.list(child_kind, ns):
        for ref in deep_get(obj, "metadata.ownerReferences", []) or []:
            if ref.get("kind") == owner_kind and ref.get("name") == owner_name:
                out.append(obj)
                break
    return out


# --------------------------------------------------------------------------
# Services / endpoints / ingress
# --------------------------------------------------------------------------
def reconcile_service(cluster: Cluster, svc: dict) -> None:
    name = deep_get(svc, "metadata.name")
    ns = deep_get(svc, "metadata.namespace", "default")
    selector = deep_get(svc, "spec.selector", {}) or {}
    endpoints = []
    if selector:
        for pod in cluster.list("Pod", ns):
            if not match_labels(deep_get(pod, "metadata.labels", {}) or {}, selector):
                continue
            if deep_get(pod, "status.phase") != "Running" or \
                    not deep_get(pod, "status.ready"):
                continue
            endpoints.append({"ip": deep_get(pod, "status.podIP"),
                              "targetRef": deep_get(pod, "metadata.name"),
                              "nodeName": deep_get(pod, "spec.nodeName")})
    svc.setdefault("status", {})["endpoints"] = endpoints
    if selector and not endpoints:
        cluster.record("Warning", "NoEndpoints",
                       f"Service {name} has no ready endpoints -- check that "
                       "spec.selector matches your pod labels (handbook page 7)",
                       f"service/{name}", ns)


def reconcile_ingress(cluster: Cluster, ing: dict) -> None:
    ns = deep_get(ing, "metadata.namespace", "default")
    name = deep_get(ing, "metadata.name")
    controllers = cluster.list("Pod", "ingress-nginx") or [
        p for p in cluster.list("Pod")
        if "ingress" in str(deep_get(p, "metadata.labels", {}))]
    address = "203.0.113.10" if controllers else ""
    missing = []
    for rule in deep_get(ing, "spec.rules", []) or []:
        for path in deep_get(rule, "http.paths", []) or []:
            svc_name = deep_get(path, "backend.service.name")
            if svc_name and not cluster.get("Service", ns, svc_name):
                missing.append(svc_name)
    ing.setdefault("status", {})["loadBalancer"] = {
        "ingress": [{"ip": address}] if address else []}
    ing["status"]["missingBackends"] = missing
    if missing:
        cluster.record("Warning", "MissingBackend",
                       f"Ingress {name} references unknown service(s): "
                       f"{', '.join(sorted(set(missing)))}", f"ingress/{name}", ns)
    elif not controllers:
        cluster.record("Warning", "NoIngressController",
                       f"Ingress {name} has no controller -- an Ingress resource does "
                       "nothing without an Ingress Controller (handbook page 32)",
                       f"ingress/{name}", ns)


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------
def reconcile_pvc(cluster: Cluster, pvc: dict) -> None:
    name = deep_get(pvc, "metadata.name")
    ns = deep_get(pvc, "metadata.namespace", "default")
    if deep_get(pvc, "status.phase") == "Bound":
        return
    want = parse_mem(deep_get(pvc, "spec.resources.requests.storage", "1Gi"))
    modes = set(deep_get(pvc, "spec.accessModes", []) or [])
    sc_name = deep_get(pvc, "spec.storageClassName")

    for pv in cluster.list("PersistentVolume"):
        if deep_get(pv, "status.phase") != "Available":
            continue
        if sc_name and deep_get(pv, "spec.storageClassName") != sc_name:
            continue
        if parse_mem(deep_get(pv, "spec.capacity.storage", "0")) < want:
            continue
        if modes and not modes.issubset(set(deep_get(pv, "spec.accessModes", []) or [])):
            continue
        pv["spec"]["claimRef"] = {"kind": "PersistentVolumeClaim", "name": name,
                                  "namespace": ns}
        pv["status"]["phase"] = "Bound"
        pvc["status"].update({"phase": "Bound",
                              "volumeName": deep_get(pv, "metadata.name"),
                              "capacity": deep_get(pv, "spec.capacity")})
        cluster.record("Normal", "Bound",
                       f"Bound PVC {name} to PV {deep_get(pv, 'metadata.name')}",
                       f"persistentvolumeclaim/{name}", ns)
        return

    storage_class = None
    if sc_name:
        storage_class = cluster.get("StorageClass", "", sc_name)
    else:
        for sc in cluster.list("StorageClass"):
            annotations = deep_get(sc, "metadata.annotations", {}) or {}
            if str(annotations.get("storageclass.kubernetes.io/is-default-class")) \
                    .lower() == "true":
                storage_class = sc
                break
    if storage_class is not None:
        binding_mode = deep_get(storage_class, "volumeBindingMode", "Immediate")
        if binding_mode == "WaitForFirstConsumer" and not _pvc_in_use(cluster, ns, name):
            pvc["status"]["phase"] = "Pending"
            pvc["status"]["message"] = "waiting for first consumer to be created"
            return
        pv_name = f"pvc-{deep_get(pvc, 'metadata.uid', '00000000')[:8]}"
        pv = {"apiVersion": "v1", "kind": "PersistentVolume",
              "metadata": {"name": pv_name, "uid": cluster.next_uid(),
                           "creationTick": cluster.tick,
                           "labels": {"provisioned-by": "lab"}, "annotations": {}},
              "spec": {"capacity": {"storage": deep_get(
                           pvc, "spec.resources.requests.storage", "1Gi")},
                       "accessModes": list(modes) or ["ReadWriteOnce"],
                       "storageClassName": deep_get(storage_class, "metadata.name"),
                       "persistentVolumeReclaimPolicy": deep_get(
                           storage_class, "reclaimPolicy", "Delete"),
                       "claimRef": {"kind": "PersistentVolumeClaim", "name": name,
                                    "namespace": ns}},
              "status": {"phase": "Bound"}}
        cluster.put(pv)
        pvc["status"].update({"phase": "Bound", "volumeName": pv_name,
                              "capacity": pv["spec"]["capacity"]})
        cluster.record("Normal", "ProvisioningSucceeded",
                       f"Dynamically provisioned volume {pv_name} using "
                       f"StorageClass {deep_get(storage_class, 'metadata.name')}",
                       f"persistentvolumeclaim/{name}", ns)
        return

    pvc["status"]["phase"] = "Pending"
    cluster.record("Warning", "ProvisioningFailed",
                   f"no persistent volumes available for claim {name} and no "
                   "StorageClass is set (handbook pages 11-14)",
                   f"persistentvolumeclaim/{name}", ns)


def _pvc_in_use(cluster: Cluster, ns: str, pvc_name: str) -> bool:
    for pod in cluster.list("Pod", ns):
        for volume in deep_get(pod, "spec.volumes", []) or []:
            if deep_get(volume, "persistentVolumeClaim.claimName") == pvc_name:
                return True
    return False


# --------------------------------------------------------------------------
# autoscaling
# --------------------------------------------------------------------------
def reconcile_hpa(cluster: Cluster, hpa: dict) -> None:
    name = deep_get(hpa, "metadata.name")
    ns = deep_get(hpa, "metadata.namespace", "default")
    ref = deep_get(hpa, "spec.scaleTargetRef", {}) or {}
    target = cluster.get(ref.get("kind", "Deployment"), ns, ref.get("name"))
    if target is None:
        hpa.setdefault("status", {})["message"] = \
            f'failed to get scale target {ref.get("kind")}/{ref.get("name")}'
        cluster.record("Warning", "FailedGetScale",
                       f'HPA {name}: {ref.get("kind")}/{ref.get("name")} not found',
                       f"horizontalpodautoscaler/{name}", ns)
        return

    key = f"{ns}/{ref.get('kind')}/{ref.get('name')}"
    current_load = cluster.load.get(key, 20.0)
    target_pct = 80
    for metric in deep_get(hpa, "spec.metrics", []) or []:
        if deep_get(metric, "resource.name") == "cpu":
            target_pct = int(deep_get(metric,
                                      "resource.target.averageUtilization", 80) or 80)
    if not deep_get(hpa, "spec.metrics"):
        target_pct = int(deep_get(hpa, "spec.targetCPUUtilizationPercentage", 80) or 80)

    replicas = int(deep_get(target, "spec.replicas", 1) or 1)
    mini = int(deep_get(hpa, "spec.minReplicas", 1) or 1)
    maxi = int(deep_get(hpa, "spec.maxReplicas", 10) or 10)
    desired = replicas
    if current_load > 0:
        raw = replicas * (current_load / max(1, target_pct))
        desired = int(raw + 0.999) if raw > replicas else int(raw)
    desired = max(mini, min(maxi, max(1, desired)))

    hpa.setdefault("status", {}).update({
        "currentReplicas": replicas, "desiredReplicas": desired,
        "currentCPUUtilizationPercentage": int(current_load),
        "targetCPUUtilizationPercentage": target_pct})

    cooldown = 3 if desired > replicas else 8   # scale up fast, down slow
    last = deep_get(hpa, "status.lastScaleTick", -999)
    if desired != replicas and cluster.tick - last >= cooldown:
        target.setdefault("spec", {})["replicas"] = desired
        hpa["status"]["lastScaleTick"] = cluster.tick
        cluster.record("Normal", "SuccessfulRescale",
                       f"New size: {desired}; reason: cpu resource utilization "
                       f"({int(current_load)}%) above target ({target_pct}%)"
                       if desired > replicas else
                       f"New size: {desired}; reason: All metrics below target",
                       f"horizontalpodautoscaler/{name}", ns)


def reconcile_vpa(cluster: Cluster, vpa: dict) -> None:
    """VPA in recommendation mode (handbook page 29)."""
    ns = deep_get(vpa, "metadata.namespace", "default")
    ref = deep_get(vpa, "spec.targetRef", {}) or {}
    target = cluster.get(ref.get("kind", "Deployment"), ns, ref.get("name"))
    if target is None:
        return
    recs = []
    for container in deep_get(target, "spec.template.spec.containers", []) or []:
        req = deep_get(container, "resources.requests", {}) or {}
        cpu = parse_cpu(req.get("cpu", "100m")) or 100
        mem = parse_mem(req.get("memory", "128Mi")) or 128
        key = f"{ns}/{ref.get('kind')}/{ref.get('name')}"
        load = cluster.load.get(key, 20.0) / 100.0
        recs.append({"containerName": container.get("name"),
                     "target": {"cpu": fmt_cpu(max(50, cpu * max(0.5, load * 1.4))),
                                "memory": f"{int(max(64, mem * max(0.6, load * 1.3)))}Mi"}})
    vpa.setdefault("status", {})["recommendation"] = {"containerRecommendations": recs}


# --------------------------------------------------------------------------
# pod lifecycle
# --------------------------------------------------------------------------
def _image_is_bad(pod: dict) -> Optional[str]:
    for container in (deep_get(pod, "spec.initContainers", []) or []) + \
            (deep_get(pod, "spec.containers", []) or []):
        image = str(container.get("image", ""))
        if any(marker in image.lower() for marker in BAD_IMAGE_MARKERS):
            return image
    return None


def _pull_problem(cluster: Cluster, pod: dict) -> Optional[tuple]:
    """(image, reason) when the kubelet cannot pull an image for this pod."""
    from .admin import image_known
    ns = deep_get(pod, "metadata.namespace", "default")
    secrets = [deep_get(cluster.get("Secret", ns, ref.get("name")) or {}, "type")
               for ref in deep_get(pod, "spec.imagePullSecrets", []) or []]
    has_creds = "kubernetes.io/dockerconfigjson" in secrets
    for container in (deep_get(pod, "spec.initContainers", []) or []) + \
            (deep_get(pod, "spec.containers", []) or []):
        image = str(container.get("image", ""))
        if any(marker in image.lower() for marker in BAD_IMAGE_MARKERS):
            return image, "notfound"
        exists, private = image_known(cluster, image)
        if not exists:
            return image, "notfound"
        if private and not has_creds:
            return image, "unauthorized"
    return None


def advance_pod(cluster: Cluster, pod: dict) -> None:
    name = deep_get(pod, "metadata.name")
    ns = deep_get(pod, "metadata.namespace", "default")
    status = pod.setdefault("status", {})
    phase = status.get("phase", "Pending")

    if phase in ("Succeeded", "Failed"):
        return

    if not deep_get(pod, "spec.nodeName"):
        schedule_pod(cluster, pod)
        if not deep_get(pod, "spec.nodeName"):
            return
    elif "scheduledTick" not in status:
        # DaemonSet pods (and pods you pin with spec.nodeName) bypass the
        # scheduler, so nothing had stamped their start time -- without this
        # they sit in ContainerCreating for ever.
        node = cluster.get("Node", "", deep_get(pod, "spec.nodeName"))
        status["scheduledTick"] = cluster.tick
        status["hostIP"] = deep_get(node or {}, "status.addresses.0.address",
                                    "10.0.0.1")
        cluster.record("Normal", "Scheduled",
                       f"Successfully assigned {ns}/{name} to "
                       f"{deep_get(pod, 'spec.nodeName')}", f"pod/{name}", ns)

    # mounted PVC must be bound before the kubelet can start the pod
    for volume in deep_get(pod, "spec.volumes", []) or []:
        claim = deep_get(volume, "persistentVolumeClaim.claimName")
        if claim:
            pvc = cluster.get("PersistentVolumeClaim", ns, claim)
            if pvc is None or deep_get(pvc, "status.phase") != "Bound":
                status["phase"] = "Pending"
                status["reason"] = "ContainerCreating"
                status["message"] = f'waiting for PVC "{claim}" to be bound'
                status["ready"] = False
                return
    for volume in deep_get(pod, "spec.volumes", []) or []:
        cm = deep_get(volume, "configMap.name")
        secret = deep_get(volume, "secret.secretName")
        if cm and not cluster.get("ConfigMap", ns, cm):
            status.update({"phase": "Pending", "reason": "CreateContainerConfigError",
                           "message": f'configmap "{cm}" not found', "ready": False})
            cluster.record("Warning", "FailedMount",
                           f'MountVolume.SetUp failed for volume: configmap "{cm}" '
                           "not found", f"pod/{name}", ns)
            return
        if secret and not cluster.get("Secret", ns, secret):
            status.update({"phase": "Pending", "reason": "CreateContainerConfigError",
                           "message": f'secret "{secret}" not found', "ready": False})
            return

    chaos = cluster.chaos.get(f"{ns}/pod/{name}") or \
        cluster.chaos.get(f'{ns}/pod/{label(pod, "app")}')
    problem = _pull_problem(cluster, pod)
    bad_image = problem[0] if problem else None
    pull_reason = problem[1] if problem else ""
    if problem and chaos is None:
        chaos = "imagepull"

    scheduled = status.get("scheduledTick", cluster.tick)
    elapsed = cluster.tick - scheduled

    if chaos == "imagepull":
        detail = ("pull access denied, repository does not exist or may require "
                  "authorization -- add an imagePullSecret"
                  if pull_reason == "unauthorized" else "not found in registry")
        status.update({"phase": "Pending", "reason": "ImagePullBackOff",
                       "message": f'Back-off pulling image "{bad_image or "unknown"}"'
                                  f": {detail}",
                       "ready": False})
        status["restartCount"] = status.get("restartCount", 0)
        cluster.record("Warning", "Failed",
                       f'Failed to pull image "{bad_image or "unknown"}": {detail}',
                       f"pod/{name}", ns)
        return

    init_containers = deep_get(pod, "spec.initContainers", []) or []
    init_time = INIT_DELAY * len(init_containers)
    if elapsed < init_time:
        current = min(len(init_containers) - 1, elapsed // INIT_DELAY)
        status.update({"phase": "Pending",
                       "reason": f"Init:{current}/{len(init_containers)}",
                       "ready": False})
        return

    if elapsed < init_time + READY_DELAY:
        status.update({"phase": "Pending", "reason": "ContainerCreating",
                       "ready": False})
        return

    if chaos == "crash":
        status["restartCount"] = status.get("restartCount", 0) + 1
        status.update({"phase": "Running", "reason": "CrashLoopBackOff", "ready": False,
                       "message": "back-off restarting failed container"})
        status.setdefault("podIP", _pod_ip(cluster))
        cluster.record("Warning", "BackOff",
                       f"Back-off restarting failed container in pod {name}",
                       f"pod/{name}", ns)
        return
    if chaos == "oom":
        status["restartCount"] = status.get("restartCount", 0) + 1
        status.update({"phase": "Running", "reason": "OOMKilled", "ready": False,
                       "message": "container exceeded its memory limit"})
        cluster.record("Warning", "OOMKilled",
                       f"Container in pod {name} was OOMKilled -- raise "
                       "resources.limits.memory (handbook page 26)", f"pod/{name}", ns)
        return
    if chaos == "notready":
        status.update({"phase": "Running", "reason": "ReadinessProbeFailed",
                       "ready": False,
                       "message": "readiness probe failed: HTTP probe returned 503"})
        status.setdefault("podIP", _pod_ip(cluster))
        cluster.record("Warning", "Unhealthy",
                       f"Readiness probe failed for pod {name}: connection refused",
                       f"pod/{name}", ns)
        return

    if status.get("jobPod"):
        if elapsed >= init_time + READY_DELAY + JOB_DURATION:
            status.update({"phase": "Succeeded", "ready": False,
                           "reason": "Completed"})
            return

    status.setdefault("podIP", _pod_ip(cluster))
    status["phase"] = "Running"
    status.pop("message", None)

    # readiness probe adds initialDelaySeconds before Ready
    delay = 0
    for container in deep_get(pod, "spec.containers", []) or []:
        probe = container.get("readinessProbe") or {}
        delay = max(delay, int(probe.get("initialDelaySeconds", 0) or 0))
    if elapsed < init_time + READY_DELAY + min(delay, 10):
        status["ready"] = False
        status["reason"] = "Running"
        return
    if not status.get("ready"):
        cluster.record("Normal", "Started", f"Started container in pod {name}",
                       f"pod/{name}", ns)
    status["ready"] = True
    status["reason"] = "Running"


# --------------------------------------------------------------------------
# node metrics + top
# --------------------------------------------------------------------------
def update_node_status(cluster: Cluster) -> None:
    for node in cluster.list("Node"):
        name = deep_get(node, "metadata.name")
        cap_cpu, cap_mem = node_allocatable(node)
        used_cpu, used_mem = node_usage(cluster, name)
        node.setdefault("status", {})["usage"] = {
            "cpu": used_cpu, "memory": used_mem,
            "cpuPct": int(100 * used_cpu / max(cap_cpu, 1)),
            "memPct": int(100 * used_mem / max(cap_mem, 1)),
            "pods": len([p for p in cluster.list("Pod")
                         if deep_get(p, "spec.nodeName") == name and _alive(p)])}


# --------------------------------------------------------------------------
# main loop
# --------------------------------------------------------------------------
ORDER = ["Namespace", "StorageClass", "PersistentVolume", "PersistentVolumeClaim",
         "CronJob", "Job", "Deployment", "ReplicaSet", "StatefulSet", "DaemonSet",
         "Service", "Ingress", "HorizontalPodAutoscaler", "VerticalPodAutoscaler"]

HANDLERS = {
    "PersistentVolumeClaim": reconcile_pvc,
    "CronJob": reconcile_cronjob,
    "Job": reconcile_job,
    "Deployment": reconcile_deployment,
    "ReplicaSet": reconcile_replicaset,
    "StatefulSet": reconcile_statefulset,
    "DaemonSet": reconcile_daemonset,
    "Service": reconcile_service,
    "Ingress": reconcile_ingress,
    "HorizontalPodAutoscaler": reconcile_hpa,
    "VerticalPodAutoscaler": reconcile_vpa,
}


def reconcile(cluster: Cluster, ticks: int = 1) -> None:
    """Advance the whole cluster by ``ticks`` control-loop iterations."""
    for _ in range(max(1, ticks)):
        cluster.tick += 1
        for kind in ORDER:
            handler = HANDLERS.get(kind)
            if handler is None:
                continue
            for obj in list(cluster.list(kind)):
                try:
                    handler(cluster, obj)
                except Exception as exc:      # a controller crash must not kill the lab
                    cluster.record("Warning", "ControllerError",
                                   f"{kind} controller error: {exc}",
                                   f"{kind.lower()}/{deep_get(obj, 'metadata.name')}",
                                   deep_get(obj, "metadata.namespace", "default"))
        for pod in list(cluster.list("Pod")):
            try:
                advance_pod(cluster, pod)
            except Exception as exc:
                cluster.record("Warning", "KubeletError", str(exc),
                               f"pod/{deep_get(pod, 'metadata.name')}",
                               deep_get(pod, "metadata.namespace", "default"))
        # second pass so Services and workload status see freshly-ready pods
        # in the same tick instead of lagging one behind
        for kind in ("ReplicaSet", "Deployment", "StatefulSet", "DaemonSet", "Job"):
            for obj in list(cluster.list(kind)):
                try:
                    HANDLERS[kind](cluster, obj)
                except Exception:
                    pass
        for svc in list(cluster.list("Service")):
            reconcile_service(cluster, svc)
        update_node_status(cluster)
    for listener in list(cluster.listeners):
        try:
            listener(cluster)
        except Exception:
            pass
