"""Bootstraps a fresh lab cluster: control plane, worker nodes, system namespaces."""
from __future__ import annotations

from .model import Cluster, deep_get

NODE_PRESETS = [
    # name, role, cpu, memory, labels, taints
    ("lab-control-plane", "control-plane", "4", "8Gi",
     {"kubernetes.io/hostname": "lab-control-plane",
      "node-role.kubernetes.io/control-plane": "",
      "topology.kubernetes.io/zone": "zone-a", "kubernetes.io/os": "linux"},
     [{"key": "node-role.kubernetes.io/control-plane", "effect": "NoSchedule"}]),
    ("lab-worker-1", "worker", "4", "8Gi",
     {"kubernetes.io/hostname": "lab-worker-1", "disktype": "ssd",
      "topology.kubernetes.io/zone": "zone-a", "kubernetes.io/os": "linux",
      "node.kubernetes.io/instance-type": "m5.xlarge"}, []),
    ("lab-worker-2", "worker", "4", "8Gi",
     {"kubernetes.io/hostname": "lab-worker-2", "disktype": "ssd",
      "topology.kubernetes.io/zone": "zone-b", "kubernetes.io/os": "linux",
      "node.kubernetes.io/instance-type": "m5.xlarge"}, []),
    ("lab-worker-3", "worker", "2", "4Gi",
     {"kubernetes.io/hostname": "lab-worker-3", "disktype": "hdd", "gpu": "true",
      "topology.kubernetes.io/zone": "zone-c", "kubernetes.io/os": "linux",
      "node.kubernetes.io/instance-type": "g4dn.xlarge"},
     [{"key": "gpu", "value": "true", "effect": "NoSchedule"}]),
]

SYSTEM_NAMESPACES = ["default", "kube-system", "kube-public", "kube-node-lease"]


def _namespace(name: str, cluster: Cluster) -> dict:
    return {"apiVersion": "v1", "kind": "Namespace",
            "metadata": {"name": name, "labels": {"kubernetes.io/metadata.name": name},
                         "annotations": {}, "uid": cluster.next_uid(),
                         "creationTick": 0},
            "spec": {}, "status": {"phase": "Active"}}


def build_cluster(name: str = "lab-cluster", workers: int = 3) -> Cluster:
    cluster = Cluster(name)
    for ns in SYSTEM_NAMESPACES:
        cluster.put(_namespace(ns, cluster))

    presets = [NODE_PRESETS[0]] + NODE_PRESETS[1:1 + max(1, workers)]
    for index, (node_name, role, cpu, memory, labels, taints) in enumerate(presets):
        node = {
            "apiVersion": "v1", "kind": "Node",
            "metadata": {"name": node_name, "labels": dict(labels), "annotations": {},
                         "uid": cluster.next_uid(), "creationTick": 0},
            "spec": {"taints": list(taints), "unschedulable": False,
                     "podCIDR": f"10.244.{index}.0/24"},
            "status": {
                "ready": True, "role": role,
                "capacity": {"cpu": cpu, "memory": memory, "pods": "110"},
                "allocatable": {"cpu": cpu, "memory": memory, "pods": "110"},
                "addresses": [{"type": "InternalIP", "address": f"172.18.0.{index + 2}"},
                              {"type": "Hostname", "address": node_name}],
                "nodeInfo": {"kubeletVersion": "v1.30.2",
                             "containerRuntimeVersion": "containerd://1.7.18",
                             "osImage": "Ubuntu 22.04.4 LTS",
                             "kernelVersion": "6.5.0-lab"},
                "usage": {"cpu": 0, "memory": 0, "cpuPct": 0, "memPct": 0, "pods": 0}},
        }
        cluster.put(node)

    # a default StorageClass so dynamic provisioning works out of the box (page 14)
    cluster.put({
        "apiVersion": "storage.k8s.io/v1", "kind": "StorageClass",
        "metadata": {"name": "standard", "uid": cluster.next_uid(), "creationTick": 0,
                     "labels": {},
                     "annotations": {
                         "storageclass.kubernetes.io/is-default-class": "true"}},
        "provisioner": "lab.local/hostpath", "reclaimPolicy": "Delete",
        "volumeBindingMode": "Immediate", "allowVolumeExpansion": True,
        "parameters": {"type": "hostpath"}, "spec": {}, "status": {}})
    cluster.put({
        "apiVersion": "storage.k8s.io/v1", "kind": "StorageClass",
        "metadata": {"name": "fast-ssd", "uid": cluster.next_uid(), "creationTick": 0,
                     "labels": {}, "annotations": {}},
        "provisioner": "lab.local/ssd", "reclaimPolicy": "Delete",
        "volumeBindingMode": "WaitForFirstConsumer", "allowVolumeExpansion": True,
        "parameters": {"type": "gp3", "fsType": "ext4"}, "spec": {}, "status": {}})

    # default ServiceAccount in every namespace (page 35)
    for ns in SYSTEM_NAMESPACES:
        cluster.put({"apiVersion": "v1", "kind": "ServiceAccount",
                     "metadata": {"name": "default", "namespace": ns,
                                  "uid": cluster.next_uid(), "creationTick": 0,
                                  "labels": {}, "annotations": {}},
                     "spec": {}, "status": {}})

    # built-in ClusterRoles (page 34)
    for role_name, verbs, resources in (
            ("cluster-admin", ["*"], ["*"]),
            ("view", ["get", "list", "watch"], ["*"]),
            ("edit", ["get", "list", "watch", "create", "update", "patch", "delete"],
             ["pods", "deployments", "services", "configmaps", "secrets"])):
        cluster.put({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole",
                     "metadata": {"name": role_name, "uid": cluster.next_uid(),
                                  "creationTick": 0, "labels": {}, "annotations": {}},
                     "rules": [{"apiGroups": ["*"], "resources": resources,
                                "verbs": verbs}], "spec": {}, "status": {}})

    cluster.record("Normal", "ClusterReady",
                   f"Lab cluster '{name}' ready with {len(presets)} nodes",
                   "cluster", "default")
    return cluster


def add_node(cluster: Cluster, name: str, cpu: str = "4", memory: str = "8Gi",
             zone: str = "zone-a") -> dict:
    index = len(cluster.list("Node"))
    node = {"apiVersion": "v1", "kind": "Node",
            "metadata": {"name": name,
                         "labels": {"kubernetes.io/hostname": name,
                                    "topology.kubernetes.io/zone": zone,
                                    "kubernetes.io/os": "linux"},
                         "annotations": {}, "uid": cluster.next_uid(),
                         "creationTick": cluster.tick},
            "spec": {"taints": [], "unschedulable": False},
            "status": {"ready": True, "role": "worker",
                       "capacity": {"cpu": cpu, "memory": memory, "pods": "110"},
                       "allocatable": {"cpu": cpu, "memory": memory, "pods": "110"},
                       "addresses": [{"type": "InternalIP",
                                      "address": f"172.18.0.{index + 2}"}],
                       "nodeInfo": {"kubeletVersion": "v1.30.2",
                                    "containerRuntimeVersion": "containerd://1.7.18",
                                    "osImage": "Ubuntu 22.04.4 LTS"},
                       "usage": {"cpu": 0, "memory": 0, "cpuPct": 0, "memPct": 0,
                                 "pods": 0}}}
    cluster.put(node)
    cluster.record("Normal", "NodeAdded", f"Node {name} joined the cluster",
                   f"node/{name}", "default")
    return node
