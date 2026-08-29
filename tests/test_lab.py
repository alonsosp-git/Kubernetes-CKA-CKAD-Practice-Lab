"""Test suite for k8s-practice-lab.

    python -m unittest discover -s tests -v
    python tests/test_lab.py                 (same thing, no pytest needed)

Covers the YAML parser, the API server's validation and RBAC, the scheduler,
every controller, the kubectl surface, helm/kustomize, the topology builder,
the SVG exporter, the web API and all 24 lab scripts end to end.
"""
from __future__ import annotations

import glob
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from k8slab import export, handbook, labs as lab_index, miniyaml, topology   # noqa: E402
from k8slab.apiserver import ApiError, apply, can_i                          # noqa: E402
from k8slab.cluster_factory import build_cluster                             # noqa: E402
from k8slab.controllers import reconcile                                     # noqa: E402
from k8slab.kubectl import Shell                                             # noqa: E402
from k8slab.model import deep_get, parse_cpu, parse_mem, resolve_kind        # noqa: E402


def pages_imported() -> bool:
    """True when the user has run tools/import_handbook.py.

    The page scans are somebody else's copyrighted book, so the repository
    ships without them and every test that needs one skips instead of failing.
    """
    directory = os.path.join(ROOT, "handbook", "pages")
    return os.path.isdir(directory) and any(
        n.endswith(".png") for n in os.listdir(directory))


def scratch_state() -> str:
    """A private progress file per test server, so they never collide."""
    return tempfile.mkdtemp(prefix="k8slab-test-state-")


# ---------------------------------------------------------------------------
# HTTP helper.
#
# The server mints a session token at start-up and refuses every request that
# does not carry it, so the tests authenticate the same way the page does: the
# token in a header, and (for the plain navigations a browser cannot add a
# header to) in the query string as well.
# ---------------------------------------------------------------------------
def start_web(shell_obj, port: int):
    from k8slab.webui import serve
    url = serve(shell_obj, state_dir=scratch_state(), port=port,
                host="127.0.0.1", background=True)
    parts = urllib.parse.urlsplit(url)
    token = urllib.parse.parse_qs(parts.query).get("token", [""])[0]
    return f"{parts.scheme}://{parts.netloc}", token


def fetch(base: str, token: str, path: str, payload=None, timeout: int = 30):
    """One authenticated request. Returns the open response object."""
    url = base + path
    if token:
        url += ("&" if "?" in path else "?") + "token=" + token
    headers = {"X-K8SLab-Token": token} if token else {}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    return urllib.request.urlopen(request, timeout=timeout)


def shell() -> Shell:
    return Shell(base_dir=ROOT)


def run(sh: Shell, *commands):
    out = []
    for command in commands:
        result = sh.run(command)
        out.append(result)
    return out[-1] if len(out) == 1 else out


# ---------------------------------------------------------------------------
class TestMiniYaml(unittest.TestCase):
    def test_nested_maps_and_lists(self):
        doc = miniyaml.load("""
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  labels:
    app: web
    tier: frontend
spec:
  replicas: 3
  template:
    spec:
      containers:
        - name: web
          image: nginx:1.25
          ports:
            - containerPort: 80
""")
        self.assertEqual(doc["kind"], "Deployment")
        self.assertEqual(doc["spec"]["replicas"], 3)
        self.assertEqual(deep_get(doc, "metadata.labels.tier"), "frontend")
        container = doc["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(container["image"], "nginx:1.25")
        self.assertEqual(container["ports"][0]["containerPort"], 80)

    def test_multidoc_flow_and_block_scalars(self):
        docs = miniyaml.load_all("""
kind: A
data:
  list: [1, 2, 3]
  map: {x: 1, y: two}
  text: |
    line one
    line two
---
kind: B
enabled: true
missing: null
""")
        self.assertEqual(len(docs), 2)
        self.assertEqual(docs[0]["data"]["list"], [1, 2, 3])
        self.assertEqual(docs[0]["data"]["map"]["y"], "two")
        self.assertIn("line two", docs[0]["data"]["text"])
        self.assertIs(docs[1]["enabled"], True)
        self.assertIsNone(docs[1]["missing"])

    def test_roundtrip(self):
        original = {"kind": "Pod", "metadata": {"name": "x", "labels": {"a": "b"}},
                    "spec": {"containers": [{"name": "c", "image": "nginx:1.25"}]}}
        again = miniyaml.load(miniyaml.dump(original))
        self.assertEqual(again["metadata"]["name"], "x")
        self.assertEqual(again["spec"]["containers"][0]["image"], "nginx:1.25")

    def test_quantities(self):
        self.assertEqual(parse_cpu("500m"), 500)
        self.assertEqual(parse_cpu("2"), 2000)
        self.assertEqual(parse_mem("1Gi"), 1024)
        self.assertEqual(parse_mem("512Mi"), 512)


# ---------------------------------------------------------------------------
class TestApiServer(unittest.TestCase):
    def setUp(self):
        self.cluster = build_cluster()

    def test_selector_mismatch_is_rejected(self):
        bad = {"apiVersion": "apps/v1", "kind": "Deployment",
               "metadata": {"name": "bad"},
               "spec": {"selector": {"matchLabels": {"app": "a"}},
                        "template": {"metadata": {"labels": {"app": "b"}},
                                     "spec": {"containers": [
                                         {"name": "c", "image": "nginx"}]}}}}
        with self.assertRaises(ApiError):
            apply(self.cluster, bad)

    def test_missing_namespace_is_rejected(self):
        pod = {"apiVersion": "v1", "kind": "Pod",
               "metadata": {"name": "p", "namespace": "nope"},
               "spec": {"containers": [{"name": "c", "image": "nginx"}]}}
        with self.assertRaises(ApiError):
            apply(self.cluster, pod)

    def test_invalid_name_is_rejected(self):
        pod = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "Bad_Name"},
               "spec": {"containers": [{"name": "c", "image": "nginx"}]}}
        with self.assertRaises(ApiError):
            apply(self.cluster, pod)

    def test_defaulting(self):
        apply(self.cluster, {"apiVersion": "v1", "kind": "Pod",
                             "metadata": {"name": "p"},
                             "spec": {"containers": [{"name": "c", "image": "nginx"}]}})
        pod = self.cluster.get("Pod", "default", "p")
        self.assertEqual(deep_get(pod, "spec.containers.0.image"), "nginx:latest")
        self.assertEqual(deep_get(pod, "spec.restartPolicy"), "Always")
        self.assertEqual(deep_get(pod, "spec.serviceAccountName"), "default")

    def test_nodeport_range(self):
        svc = {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "s"},
               "spec": {"type": "NodePort", "selector": {"app": "x"},
                        "ports": [{"port": 80, "nodePort": 8080}]}}
        with self.assertRaises(ApiError):
            apply(self.cluster, svc)

    def test_rbac_default_deny_and_grant(self):
        self.cluster.rbac_enforced = True
        self.cluster.current_user = "bob"
        self.assertFalse(can_i(self.cluster, "list", "pods", "default"))
        self.cluster.current_user = "admin"
        apply(self.cluster, {"apiVersion": "rbac.authorization.k8s.io/v1",
                             "kind": "Role", "metadata": {"name": "r"},
                             "rules": [{"apiGroups": [""], "resources": ["pods"],
                                        "verbs": ["get", "list"]}]})
        apply(self.cluster, {"apiVersion": "rbac.authorization.k8s.io/v1",
                             "kind": "RoleBinding", "metadata": {"name": "rb"},
                             "roleRef": {"kind": "Role", "name": "r"},
                             "subjects": [{"kind": "User", "name": "bob"}]})
        self.assertTrue(can_i(self.cluster, "list", "pods", "default", "bob"))
        self.assertFalse(can_i(self.cluster, "delete", "pods", "default", "bob"))


# ---------------------------------------------------------------------------
class TestControllers(unittest.TestCase):
    def test_deployment_chain_and_scaling(self):
        sh = shell()
        sh.run("kubectl create deployment web --image=nginx:1.25 --replicas=3")
        self.assertEqual(len(sh.cluster.list("ReplicaSet", "default")), 1)
        self.assertEqual(len(sh.cluster.list("Pod", "default")), 3)
        sh.run("kubectl scale deploy/web --replicas=5")
        self.assertEqual(len(sh.cluster.list("Pod", "default")), 5)
        sh.run("kubectl scale deploy/web --replicas=1")
        self.assertEqual(len(sh.cluster.list("Pod", "default")), 1)

    def test_pods_become_ready_and_get_ips(self):
        sh = shell()
        sh.run("kubectl create deployment web --image=nginx:1.25 --replicas=2")
        reconcile(sh.cluster, 8)
        pods = sh.cluster.list("Pod", "default")
        self.assertTrue(all(deep_get(p, "status.ready") for p in pods))
        self.assertTrue(all(deep_get(p, "status.podIP") for p in pods))
        self.assertTrue(all(deep_get(p, "spec.nodeName") for p in pods))

    def test_rolling_update_creates_second_replicaset_then_undo(self):
        sh = shell()
        sh.run("kubectl create deployment web --image=nginx:1.25 --replicas=2")
        reconcile(sh.cluster, 8)
        sh.run("kubectl set image deploy/web web=nginx:1.27")
        reconcile(sh.cluster, 20)
        self.assertGreaterEqual(len(sh.cluster.list("ReplicaSet", "default")), 2)
        deployment = sh.cluster.get("Deployment", "default", "web")
        self.assertEqual(deep_get(deployment, "spec.template.spec.containers.0.image"),
                         "nginx:1.27")
        sh.run("kubectl rollout undo deploy/web")
        reconcile(sh.cluster, 20)
        deployment = sh.cluster.get("Deployment", "default", "web")
        self.assertEqual(deep_get(deployment, "spec.template.spec.containers.0.image"),
                         "nginx:1.25")

    def test_ownership_cascade_delete(self):
        sh = shell()
        sh.run("kubectl create deployment web --image=nginx:1.25 --replicas=3")
        sh.run("kubectl delete deploy web")
        self.assertEqual(sh.cluster.list("Pod", "default"), [])
        self.assertEqual(sh.cluster.list("ReplicaSet", "default"), [])

    def test_service_endpoints_follow_labels(self):
        sh = shell()
        sh.run("kubectl create deployment web --image=nginx:1.25 --replicas=2")
        sh.run("kubectl expose deploy/web --port=80")
        reconcile(sh.cluster, 10)
        svc = sh.cluster.get("Service", "default", "web")
        self.assertEqual(len(deep_get(svc, "status.endpoints")), 2)
        sh.run("kubectl apply -f manifests/service-broken.yaml")
        reconcile(sh.cluster, 4)
        broken = sh.cluster.get("Service", "default", "web-broken")
        self.assertEqual(deep_get(broken, "status.endpoints"), [])

    def test_statefulset_ordering_and_pvcs(self):
        sh = shell()
        sh.run("kubectl apply -f manifests/statefulset.yaml")
        reconcile(sh.cluster, 40)
        names = sorted(deep_get(p, "metadata.name")
                       for p in sh.cluster.list("Pod", "default"))
        self.assertEqual(names, ["db-0", "db-1"])
        claims = sorted(deep_get(c, "metadata.name")
                        for c in sh.cluster.list("PersistentVolumeClaim", "default"))
        self.assertEqual(claims, ["data-db-0", "data-db-1"])
        self.assertTrue(all(deep_get(c, "status.phase") == "Bound"
                            for c in sh.cluster.list("PersistentVolumeClaim", "default")))

    def test_daemonset_pods_actually_start(self):
        """DaemonSet pods bypass the scheduler -- they must still come up."""
        sh = shell()
        sh.run("kubectl apply -f manifests/daemonset.yaml")
        reconcile(sh.cluster, 12)
        pods = sh.cluster.list("Pod", "default")
        self.assertTrue(pods)
        for pod in pods:
            self.assertEqual(deep_get(pod, "status.phase"), "Running",
                             deep_get(pod, "metadata.name"))
            self.assertTrue(deep_get(pod, "status.ready"))
            self.assertTrue(deep_get(pod, "status.podIP"))
        ds = sh.cluster.get("DaemonSet", "default", "node-exporter")
        self.assertEqual(deep_get(ds, "status.numberReady"),
                         deep_get(ds, "status.desiredNumberScheduled"))

    def test_daemonset_covers_nodes_added_later(self):
        from k8slab.admin import node_version
        sh = shell()
        sh.run("kubectl apply -f manifests/daemonset.yaml")
        reconcile(sh.cluster, 10)
        sh.run("sim node lab-worker-9 add")
        reconcile(sh.cluster, 12)
        ds = sh.cluster.get("DaemonSet", "default", "node-exporter")
        self.assertEqual(deep_get(ds, "status.desiredNumberScheduled"),
                         len(sh.cluster.list("Node")))
        self.assertEqual(deep_get(ds, "status.numberReady"),
                         len(sh.cluster.list("Node")))

    def test_pinned_pods_start_too(self):
        sh = shell()
        sh.run("kubectl apply -f manifests/pod.yaml")
        pod = sh.cluster.get("Pod", "default", "nginx-pod")
        pod["spec"]["nodeName"] = "lab-worker-2"          # pin it by hand
        reconcile(sh.cluster, 10)
        self.assertEqual(deep_get(pod, "status.phase"), "Running")

    def test_daemonset_one_pod_per_node(self):
        sh = shell()
        sh.run("kubectl apply -f manifests/daemonset.yaml")
        reconcile(sh.cluster, 8)
        nodes = len(sh.cluster.list("Node"))
        pods = sh.cluster.list("Pod", "default")
        self.assertEqual(len(pods), nodes)
        self.assertEqual(len({deep_get(p, "spec.nodeName") for p in pods}), nodes)

    def test_job_completes(self):
        sh = shell()
        sh.run("kubectl apply -f manifests/job.yaml")
        reconcile(sh.cluster, 60)
        job = sh.cluster.get("Job", "default", "data-import")
        self.assertEqual(deep_get(job, "status.succeeded"), 3)

    def test_cronjob_creates_jobs(self):
        sh = shell()
        sh.run("kubectl apply -f manifests/cronjob.yaml")
        reconcile(sh.cluster, 130)
        jobs = sh.cluster.list("Job", "default")
        self.assertGreaterEqual(len(jobs), 1)

    def test_hpa_scales_up_then_down(self):
        sh = shell()
        sh.run("kubectl create deployment web --image=nginx:1.25 --replicas=2")
        sh.run("kubectl autoscale deploy/web --cpu-percent=50 --min=2 --max=8")
        sh.run("sim load deploy/web 95")
        reconcile(sh.cluster, 30)
        self.assertGreater(deep_get(sh.cluster.get("Deployment", "default", "web"),
                                    "spec.replicas"), 2)
        sh.run("sim load deploy/web 5")
        reconcile(sh.cluster, 80)
        self.assertEqual(deep_get(sh.cluster.get("Deployment", "default", "web"),
                                  "spec.replicas"), 2)

    def test_dynamic_provisioning(self):
        sh = shell()
        sh.run("kubectl apply -f manifests/pvc-dynamic.yaml")
        reconcile(sh.cluster, 5)
        pvc = sh.cluster.get("PersistentVolumeClaim", "default", "dynamic-claim")
        self.assertEqual(deep_get(pvc, "status.phase"), "Bound")
        self.assertTrue(sh.cluster.get("PersistentVolume", "",
                                       deep_get(pvc, "status.volumeName")))

    def test_unbindable_pvc_stays_pending(self):
        sh = shell()
        sh.run("kubectl apply -f manifests/pvc-impossible.yaml")
        reconcile(sh.cluster, 5)
        pvc = sh.cluster.get("PersistentVolumeClaim", "default", "too-big")
        self.assertEqual(deep_get(pvc, "status.phase"), "Pending")


# ---------------------------------------------------------------------------
class TestScheduler(unittest.TestCase):
    def test_taint_blocks_pod_without_toleration(self):
        sh = shell()
        for node in ("lab-worker-1", "lab-worker-2"):
            sh.run(f"kubectl taint nodes {node} dedicated=payments:NoSchedule")
        sh.run("kubectl run plain --image=nginx:1.25")
        reconcile(sh.cluster, 5)
        pod = sh.cluster.get("Pod", "default", "plain")
        self.assertEqual(deep_get(pod, "status.reason"), "Unschedulable")
        self.assertIn("untolerated taint", deep_get(pod, "status.message"))

    def test_toleration_allows_scheduling(self):
        sh = shell()
        for node in ("lab-worker-1", "lab-worker-2"):
            sh.run(f"kubectl taint nodes {node} dedicated=payments:NoSchedule")
        sh.run("kubectl apply -f manifests/pod-toleration.yaml")
        reconcile(sh.cluster, 6)
        pod = sh.cluster.get("Pod", "default", "payments")
        self.assertTrue(deep_get(pod, "spec.nodeName"))

    def test_node_affinity_respected(self):
        sh = shell()
        sh.run("kubectl apply -f manifests/pod-node-affinity.yaml")
        reconcile(sh.cluster, 6)
        pod = sh.cluster.get("Pod", "default", "ssd-only")
        node = sh.cluster.get("Node", "", deep_get(pod, "spec.nodeName"))
        self.assertEqual(deep_get(node, "metadata.labels.disktype"), "ssd")

    def test_anti_affinity_spreads_replicas(self):
        sh = shell()
        sh.run("kubectl apply -f manifests/deployment-anti-affinity.yaml")
        reconcile(sh.cluster, 20)
        placed = [deep_get(p, "spec.nodeName") for p in sh.cluster.list("Pod", "default")
                  if deep_get(p, "spec.nodeName")]
        self.assertEqual(len(placed), len(set(placed)))

    def test_insufficient_resources(self):
        sh = shell()
        sh.run("kubectl apply -f manifests/pod-too-big.yaml")
        reconcile(sh.cluster, 5)
        pod = sh.cluster.get("Pod", "default", "too-big")
        self.assertIn("Insufficient", deep_get(pod, "status.message", ""))

    def test_cordon_and_drain(self):
        sh = shell()
        sh.run("kubectl create deployment web --image=nginx:1.25 --replicas=3")
        reconcile(sh.cluster, 8)
        sh.run("kubectl drain lab-worker-1 --ignore-daemonsets")
        reconcile(sh.cluster, 10)
        placed = {deep_get(p, "spec.nodeName") for p in sh.cluster.list("Pod", "default")}
        self.assertNotIn("lab-worker-1", placed)


# ---------------------------------------------------------------------------
class TestFailureModes(unittest.TestCase):
    def test_image_pull_backoff(self):
        sh = shell()
        sh.run("kubectl run typo --image=nginx:doesnotexist")
        reconcile(sh.cluster, 6)
        pod = sh.cluster.get("Pod", "default", "typo")
        self.assertEqual(deep_get(pod, "status.reason"), "ImagePullBackOff")

    def test_crashloop_and_recovery(self):
        sh = shell()
        sh.run("kubectl create deployment app --image=busybox:1.36")
        sh.run("sim chaos app crash")
        reconcile(sh.cluster, 10)
        pods = sh.cluster.list("Pod", "default")
        self.assertTrue(any(deep_get(p, "status.reason") == "CrashLoopBackOff"
                            for p in pods))
        self.assertTrue(any(deep_get(p, "status.restartCount", 0) > 0 for p in pods))
        sh.run("sim chaos app clear")
        reconcile(sh.cluster, 10)
        self.assertTrue(all(deep_get(p, "status.ready")
                            for p in sh.cluster.list("Pod", "default")))

    def test_missing_configmap_blocks_pod(self):
        sh = shell()
        sh.run("kubectl apply -f manifests/pod-missing-config.yaml")
        reconcile(sh.cluster, 6)
        pod = sh.cluster.get("Pod", "default", "missing-config")
        self.assertEqual(deep_get(pod, "status.reason"), "CreateContainerConfigError")

    def test_node_down_reschedules(self):
        sh = shell()
        sh.run("kubectl create deployment web --image=nginx:1.25 --replicas=4")
        reconcile(sh.cluster, 10)
        sh.run("sim node lab-worker-1 down")
        reconcile(sh.cluster, 12)
        pods = sh.cluster.list("Pod", "default")
        self.assertEqual(len(pods), 4)
        self.assertNotIn("lab-worker-1",
                         {deep_get(p, "spec.nodeName") for p in pods})

    def test_readiness_failure_removes_endpoint(self):
        sh = shell()
        sh.run("kubectl create deployment web --image=nginx:1.25 --replicas=2")
        sh.run("kubectl expose deploy/web --port=80")
        reconcile(sh.cluster, 10)
        sh.run("sim chaos web notready")
        reconcile(sh.cluster, 8)
        svc = sh.cluster.get("Service", "default", "web")
        self.assertEqual(deep_get(svc, "status.endpoints"), [])


# ---------------------------------------------------------------------------
class TestKubectlSurface(unittest.TestCase):
    def test_get_describe_and_output_formats(self):
        sh = shell()
        sh.run("kubectl create deployment web --image=nginx:1.25 --replicas=2")
        self.assertIn("web", sh.run("kubectl get deploy").out)
        self.assertIn("Selector", sh.run("kubectl describe deploy web").out)
        self.assertIn("kind: Deployment", sh.run("kubectl get deploy web -o yaml").out)
        payload = json.loads(sh.run("kubectl get deploy web -o json").out)
        self.assertEqual(payload["metadata"]["name"], "web")
        self.assertIn("NODE", sh.run("kubectl get pods -o wide").out)

    def test_label_selectors(self):
        sh = shell()
        sh.run("kubectl create deployment web --image=nginx:1.25 --replicas=2")
        sh.run("kubectl run other --image=nginx:1.25")
        self.assertNotIn("other", sh.run("kubectl get pods -l app=web").out)
        self.assertIn("other", sh.run("kubectl get pods -l run=other").out)

    def test_namespaces_and_context(self):
        sh = shell()
        sh.run("kubectl create namespace dev")
        sh.run("kubectl config set-context --current --namespace=dev")
        self.assertEqual(sh.cluster.current_namespace, "dev")
        sh.run("kubectl create deployment api --image=nginx:1.25")
        self.assertEqual(len(sh.cluster.list("Pod", "dev")), 1)
        self.assertEqual(len(sh.cluster.list("Pod", "default")), 0)

    def test_quota_blocks_extra_pods(self):
        sh = shell()
        sh.run("kubectl create namespace dev")
        sh.run("kubectl apply -f manifests/quota.yaml")
        for index in range(8):
            sh.run(f"kubectl run p{index} --image=nginx:1.25 -n dev")
        self.assertLessEqual(len(sh.cluster.list("Pod", "dev")), 6)

    def test_logs_exec_and_top(self):
        sh = shell()
        sh.run("kubectl create deployment web --image=nginx:1.25 --replicas=1")
        sh.run("kubectl expose deploy/web --port=80")
        sh.run("kubectl apply -f manifests/configmap.yaml")
        sh.run("kubectl apply -f manifests/pod-with-config.yaml")
        reconcile(sh.cluster, 10)
        self.assertIn("listening", sh.run("kubectl logs -l app=web").out)
        env = sh.run("kubectl exec config-consumer -- env").out
        self.assertIn("APP_PORT=8080", env)
        self.assertIn("DB_PASSWORD=s3cr3t", env)
        self.assertIn("cache.ttl",
                      sh.run("kubectl exec config-consumer -- cat "
                             "/etc/config/app.properties").out)
        self.assertIn("Hello from",
                      sh.run("kubectl exec config-consumer -- curl http://web").out)
        self.assertIn("CPU", sh.run("kubectl top nodes").out)

    def test_auth_can_i(self):
        sh = shell()
        sh.run("sim rbac on")
        sh.run("sim user bob")
        self.assertEqual(sh.run("kubectl auth can-i list pods").out, "no")
        sh.run("sim user admin")
        self.assertEqual(sh.run("kubectl auth can-i list pods").out, "yes")

    def test_as_flag_is_not_sticky(self):
        sh = shell()
        sh.run("sim rbac on")
        sh.run("kubectl auth can-i list pods --as=nobody")
        self.assertEqual(sh.cluster.current_user, "admin")

    def test_patch_and_annotate(self):
        sh = shell()
        sh.run("kubectl create deployment web --image=nginx:1.25")
        sh.run("kubectl patch deploy web -p '{\"spec\":{\"replicas\":4}}'")
        self.assertEqual(deep_get(sh.cluster.get("Deployment", "default", "web"),
                                  "spec.replicas"), 4)
        sh.run("kubectl annotate deploy/web owner=platform")
        self.assertEqual(deep_get(sh.cluster.get("Deployment", "default", "web"),
                                  "metadata.annotations.owner"), "platform")

    def test_unknown_command_is_reported(self):
        sh = shell()
        self.assertFalse(sh.run("kubectl frobnicate pods").ok)
        self.assertFalse(sh.run("kubectl get widgets").ok)
        self.assertFalse(sh.run("banana").ok)

    def test_explain_falls_back_to_handbook_topics(self):
        sh = shell()
        self.assertIn("Pod", sh.run("kubectl explain pods").out)
        self.assertTrue(sh.run("kubectl explain architecture").ok)


# ---------------------------------------------------------------------------
class TestPackaging(unittest.TestCase):
    def test_helm_install_upgrade_uninstall(self):
        sh = shell()
        self.assertTrue(sh.run("helm install shop charts/webapp").ok)
        self.assertTrue(sh.cluster.get("Deployment", "default", "shop-webapp"))
        self.assertEqual(deep_get(sh.cluster.get("Deployment", "default",
                                                 "shop-webapp"), "spec.replicas"), 2)
        sh.run("helm upgrade shop charts/webapp --set replicaCount=4")
        self.assertEqual(deep_get(sh.cluster.get("Deployment", "default",
                                                 "shop-webapp"), "spec.replicas"), 4)
        self.assertIn("shop", sh.run("helm list").out)
        sh.run("helm uninstall shop")
        self.assertIsNone(sh.cluster.get("Deployment", "default", "shop-webapp"))

    def test_kustomize_overlays(self):
        sh = shell()
        self.assertTrue(sh.run("kubectl apply -k kustomize/overlays/dev").ok)
        self.assertTrue(sh.run("kubectl apply -k kustomize/overlays/prod").ok)
        dev = sh.cluster.get("Deployment", "default", "dev-store")
        prod = sh.cluster.get("Deployment", "default", "prod-store")
        self.assertIsNotNone(dev)
        self.assertIsNotNone(prod)
        self.assertEqual(deep_get(dev, "metadata.labels.env"), "dev")
        self.assertEqual(deep_get(prod, "spec.replicas"), 3)
        self.assertEqual(deep_get(prod, "spec.template.spec.containers.0.image"),
                         "nginx:1.25.4")


# ---------------------------------------------------------------------------
class TestTopologyAndExport(unittest.TestCase):
    def _busy_cluster(self) -> Shell:
        sh = shell()
        for command in ("kubectl apply -f manifests/two-apps.yaml",
                        "kubectl apply -f manifests/ingress.yaml",
                        "kubectl apply -f manifests/configmap.yaml",
                        "kubectl apply -f manifests/pod-with-config.yaml",
                        "kubectl apply -f manifests/pvc-dynamic.yaml",
                        "kubectl apply -f manifests/pod-with-pvc.yaml",
                        "kubectl autoscale deploy/web --cpu-percent=50 --min=2 --max=6"):
            sh.run(command)
        reconcile(sh.cluster, 10)
        return sh

    def test_logical_graph_shape(self):
        sh = self._busy_cluster()
        graph = topology.build(sh.cluster, "default", "logical")
        kinds = {n.kind for n in graph.nodes}
        for expected in ("Ingress", "Service", "Deployment", "ReplicaSet", "Pod",
                         "ConfigMap", "PersistentVolumeClaim",
                         "HorizontalPodAutoscaler"):
            self.assertIn(expected, kinds)
        ids = {n.id for n in graph.nodes}
        for edge in graph.edges:                       # no dangling edges
            self.assertIn(edge.src, ids)
            self.assertIn(edge.dst, ids)
        self.assertTrue(any(e.kind == "routes" for e in graph.edges))
        self.assertTrue(any(e.kind == "selects" for e in graph.edges))
        self.assertTrue(any(e.kind == "mounts" for e in graph.edges))

    def test_no_overlapping_cards(self):
        sh = self._busy_cluster()
        for view in ("logical", "physical"):
            graph = topology.build(sh.cluster, "default", view)
            boxes = [(n.x, n.y, n.x + n.w, n.y + n.h) for n in graph.nodes]
            for i, a in enumerate(boxes):
                for b in boxes[i + 1:]:
                    overlap = (a[0] < b[2] and b[0] < a[2] and
                               a[1] < b[3] and b[1] < a[3])
                    self.assertFalse(overlap, f"{view}: cards overlap {a} {b}")

    def test_physical_pods_stay_inside_their_node_box(self):
        sh = self._busy_cluster()
        graph = topology.build(sh.cluster, None, "physical", include_system=True)
        self.assertTrue(graph.groups)
        for node in graph.nodes:
            inside = any(group["x"] <= node.x and
                         node.x + node.w <= group["x"] + group["w"] and
                         group["y"] <= node.y and
                         node.y + node.h <= group["y"] + group["h"] + 40
                         for group in graph.groups)
            self.assertTrue(inside, f"{node.name} escaped its node box")

    def test_svg_export_is_well_formed(self):
        import xml.etree.ElementTree as ET
        sh = self._busy_cluster()
        for view in ("logical", "physical"):
            svg = export.to_svg(topology.build(sh.cluster, "default", view), view)
            root = ET.fromstring(svg)
            self.assertTrue(root.tag.endswith("svg"))
            self.assertGreater(len(svg), 800)
        self.assertIn("digraph", export.to_dot(topology.build(sh.cluster, "default")))

    def test_stats_match_the_cluster(self):
        sh = self._busy_cluster()
        graph = topology.build(sh.cluster, "default", "logical")
        self.assertEqual(graph.stats["nodes"], len(sh.cluster.list("Node")))
        self.assertEqual(graph.stats["pods"], len(sh.cluster.list("Pod", "default")))


# ---------------------------------------------------------------------------
class TestHandbook(unittest.TestCase):
    def test_every_topic_is_complete(self):
        for key in handbook.ORDER:
            topic = handbook.TOPICS[key]
            self.assertTrue(topic.title, key)
            self.assertTrue(topic.summary, key)
            self.assertTrue(topic.pages, key)
            for page in topic.pages:
                self.assertTrue(1 <= page <= 54, f"{key}: page {page}")

    def test_sections_cover_every_topic(self):
        listed = {k for _, keys in handbook.SECTIONS for k in keys}
        self.assertEqual(listed, set(handbook.ORDER))

    def test_page_images_exist(self):
        if not pages_imported():
            self.skipTest("handbook pages not imported (see handbook/README.md)")
        missing = [k for k in handbook.ORDER if not handbook.TOPICS[k].page_files()]
        self.assertEqual(missing, [], f"topics with no page image: {missing}")

    def test_every_topic_has_an_original_diagram_instead(self):
        """What ships in place of the scans: our own artwork, for all 50."""
        from k8slab import diagrams
        missing = [k for k in handbook.ORDER if not diagrams.has(k)]
        self.assertEqual(missing, [], f"topics with no diagram: {missing}")

    def test_embedded_yaml_snippets_parse_and_apply(self):
        for key in handbook.ORDER:
            topic = handbook.TOPICS[key]
            if not topic.yaml:
                continue
            docs = miniyaml.load_all(topic.yaml)
            self.assertTrue(docs, key)
            sh = shell()
            sh.run("kubectl create namespace dev")
            for doc in docs:
                self.assertIsNotNone(resolve_kind(doc.get("kind", "")),
                                     f"{key}: unknown kind {doc.get('kind')}")
                try:
                    apply(sh.cluster, doc)
                except ApiError as exc:
                    self.fail(f"{key}: handbook YAML rejected: {exc}")

    def test_search(self):
        self.assertTrue(handbook.search("rbac"))
        self.assertTrue(handbook.search("autoscal"))
        self.assertEqual(len(handbook.search("")), len(handbook.ORDER))


# ---------------------------------------------------------------------------
class TestLabs(unittest.TestCase):
    EXPECTED_FAILURES = ("broken-selector.yaml",)

    def test_all_29_labs_are_indexed(self):
        self.assertEqual(len(lab_index.LABS), 29)

    def test_every_lab_file_exists(self):
        for lab in lab_index.LABS:
            self.assertTrue(lab.exists(), f"missing labs/{lab.file}")

    def test_every_lab_runs_clean(self):
        for path in sorted(glob.glob(os.path.join(ROOT, "labs", "*.kubectl"))):
            with self.subTest(lab=os.path.basename(path)):
                sh = shell()
                with open(path, encoding="utf-8") as handle:
                    for command, result in sh.run_script(handle.read()):
                        if any(marker in command for marker in self.EXPECTED_FAILURES):
                            continue
                        self.assertTrue(result.ok or result.expected,
                                        f"{os.path.basename(path)}: `{command}`\n"
                                        f"  -> {result.err}")

    def test_lab_topics_are_real(self):
        for lab in lab_index.LABS:
            self.assertIn(lab.topic, handbook.TOPICS, lab.file)


# ---------------------------------------------------------------------------
class TestWebApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shell = shell()
        cls.url, cls.token = start_web(cls.shell, 8907)
        time.sleep(0.4)

    def get(self, path):
        with fetch(self.url, self.token, path, timeout=15) as response:
            return response.status, response.read()

    def post(self, path, payload):
        with fetch(self.url, self.token, path, payload, timeout=60) as response:
            return response.status, json.loads(response.read())

    def test_page_and_assets(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Deploy this lab to a Docker container", body)
        self.assertIn(b"Cluster topology", body)
        # our own diagram is always there; the book scan only if imported
        status, body = self.get("/diagram/pods.svg")
        self.assertEqual(status, 200)
        self.assertIn(b"<svg", body)
        if pages_imported():
            status, body = self.get("/pages/page_05.png")
            self.assertEqual(status, 200)
            self.assertGreater(len(body), 1000)

    def test_json_endpoints(self):
        for path in ("/api/state", "/api/handbook", "/api/labs", "/api/topic/pods",
                     "/api/lab/03-pods.kubectl", "/api/log"):
            status, body = self.get(path)
            self.assertEqual(status, 200, path)
            json.loads(body)

    def test_run_and_topology(self):
        status, result = self.post(
            "/api/run", {"cmd": "kubectl create deployment web --image=nginx:1.25 "
                                "--replicas=2"})
        self.assertTrue(result["ok"])
        self.post("/api/tick", {"n": 8})
        status, body = self.get("/api/topology?ns=default&view=logical")
        data = json.loads(body)
        self.assertGreaterEqual(len(data["nodes"]), 3)
        self.assertIn("<svg", data["svg"])
        # the browser matches <g> elements to nodes by index -- keep them in sync
        self.assertEqual(data["svg"].count("<g"), len(data["nodes"]))

    def test_apply_yaml_and_describe(self):
        status, result = self.post("/api/apply", {
            "yaml": "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: web-test\n"
                    "data:\n  key: value\n"})
        self.assertTrue(result["ok"])
        status, body = self.get("/api/describe?id=ConfigMap/default/web-test")
        self.assertIn(b"web-test", body)

    def test_docker_endpoint_is_safe_without_docker(self):
        status, result = self.post("/api/docker", {"action": "status"})
        self.assertIn("out", result)


# ---------------------------------------------------------------------------
class TestNamespaceLanes(unittest.TestCase):
    """Anything you create is drawn in the lane of the namespace it lives in."""

    def _cluster(self) -> Shell:
        sh = shell()
        for command in ("kubectl create deployment web --image=nginx:1.25 --replicas=2",
                        "kubectl apply -f manifests/configmap.yaml",
                        "kubectl create namespace dev",
                        "kubectl create deployment api --image=nginx:1.25 -n dev",
                        "kubectl create configmap dev-cfg --from-literal=A=1 -n dev",
                        "kubectl apply -f manifests/pvc-dynamic.yaml"):
            self.assertTrue(sh.run(command).ok, command)
        reconcile(sh.cluster, 10)
        return sh

    def test_each_namespace_gets_a_lane(self):
        sh = self._cluster()
        graph = topology.build(sh.cluster, None, "logical")
        lanes = {g["name"] for g in graph.groups if g["kind"] == "Namespace"}
        self.assertEqual(lanes, {"default", "dev"})

    def test_objects_sit_in_their_own_lane(self):
        sh = self._cluster()
        graph = topology.build(sh.cluster, None, "logical")
        lanes = {g["name"]: g for g in graph.groups}
        for node in graph.nodes:
            band = node.band or node.namespace
            self.assertIn(band, lanes, f"{node.name} has no lane")
            lane = lanes[band]
            self.assertGreaterEqual(node.x, lane["x"] - 1, node.name)
            self.assertLessEqual(node.x + node.w, lane["x"] + lane["w"] + 1, node.name)
            self.assertGreaterEqual(node.y, lane["y"] - 1, node.name)
            self.assertLessEqual(node.y + node.h, lane["y"] + lane["h"] + 1, node.name)

    def test_config_created_in_dev_is_drawn_in_dev(self):
        sh = self._cluster()
        graph = topology.build(sh.cluster, None, "logical")
        node = next(n for n in graph.nodes if n.name == "dev-cfg")
        self.assertEqual(node.band or node.namespace, "dev")

    def test_bound_pv_follows_its_claim(self):
        sh = self._cluster()
        graph = topology.build(sh.cluster, None, "logical")
        volumes = [n for n in graph.nodes if n.kind == "PersistentVolume"]
        self.assertTrue(volumes)
        self.assertEqual(volumes[0].band, "default")

    def test_lanes_do_not_overlap(self):
        sh = self._cluster()
        graph = topology.build(sh.cluster, None, "logical")
        boxes = [(g["y"], g["y"] + g["h"]) for g in graph.groups]
        boxes.sort()
        for (_, bottom), (top, _) in zip(boxes, boxes[1:]):
            self.assertLessEqual(bottom, top)


class TestCommandExplanations(unittest.TestCase):
    def test_every_handbook_command_is_explained(self):
        for key in handbook.ORDER:
            topic = handbook.TOPICS[key]
            for entry in handbook.annotated_commands(topic):
                self.assertTrue(entry["why"],
                                f"{key}: no explanation for {entry['cmd']}")
                self.assertGreater(len(entry["why"]), 25, entry["cmd"])

    def test_explanations_are_specific(self):
        cases = {
            "kubectl get pods -n dev": "namespace dev",
            "kubectl scale deploy/web --replicas=5": "replica count",
            "kubectl describe pod web": "events",
            "sim load deploy/web 95": "HPA",
            "helm template shop charts/webapp": "without touching the cluster",
            "kubectl apply -k kustomize/overlays/dev": "overlay",
            "kubectl rollout undo deploy/web": "previous revision",
        }
        for command, needle in cases.items():
            self.assertIn(needle, handbook.describe_command(command), command)

    def test_unknown_commands_do_not_crash(self):
        for command in ("", "   ", "kubectl", "kubectl frobnicate", "banana split"):
            handbook.describe_command(command)


class TestProgressAndPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shell = shell()
        cls.url, cls.token = start_web(cls.shell, 8908)
        time.sleep(0.4)

    def get(self, path):
        with fetch(self.url, self.token, path, timeout=20) as response:
            return response.read()

    def post(self, path, payload):
        with fetch(self.url, self.token, path, payload, timeout=60) as response:
            return json.loads(response.read())

    def test_progress_round_trip_and_reset(self):
        self.post("/api/progress", {"done": ["pods", "services", "not-a-topic"]})
        saved = json.loads(self.get("/api/progress"))
        self.assertEqual(saved["done"], ["pods", "services"])
        self.assertEqual(saved["total"], len(handbook.ORDER))
        self.post("/api/reset", {"scope": "progress"})
        self.assertEqual(json.loads(self.get("/api/progress"))["done"], [])

    def test_topic_payload_feeds_every_tab(self):
        for key in handbook.ORDER:
            topic = json.loads(self.get("/api/topic/" + key))
            if pages_imported():
                self.assertTrue(topic["images"], key)          # Page tab
            self.assertTrue(topic["diagram"], key)             # Page tab
            self.assertTrue(topic["commands"], key)            # Notes tab
            self.assertTrue(all(c["why"] for c in topic["commands"]), key)
            self.assertTrue(topic["script"] or topic["yaml"], key)   # YAML tab

    def test_page_has_the_interactive_pieces(self):
        html = self.get("/").decode()
        for needle in ('id="lightbox"', "openLb(", 'id="split-term"',
                       'id="split-left"', 'id="split-right"', "toggleDone(",
                       'id="progfill"', 'class="cmdbox"', 'id="resetsel"',
                       "dockerDo('deploy')"):
            self.assertIn(needle, html, needle)

    def test_no_inline_event_handlers_remain(self):
        """A nonce CSP only helps if nothing relies on inline handlers.

        Every onclick=/onchange=/oninput= was moved to addEventListener; if one
        creeps back in it will silently stop working under the policy, so the
        test fails loudly instead.
        """
        html = self.get("/").decode()
        # inside a tag, i.e. an actual attribute -- not the word in a comment
        self.assertNotRegex(html, r"<[^>!]*\son(?:click|change|input|load|error)\s*=")
        self.assertIn("<script nonce=", html)

    def test_docker_without_a_daemon_explains_itself(self):
        result = self.post("/api/docker", {"action": "deploy"})
        self.assertFalse(result["ok"])
        self.assertIn("Docker", result["out"])
        self.assertIn("commands", result)


class TestDiagrams(unittest.TestCase):
    """Original vector diagrams: one per topic, valid SVG, no external artwork."""

    def test_every_topic_has_a_diagram(self):
        from k8slab import diagrams
        missing = [k for k in handbook.ORDER if not diagrams.has(k)]
        self.assertEqual(missing, [])

    def test_diagrams_are_well_formed_svg(self):
        import xml.etree.ElementTree as ET
        from k8slab import diagrams
        for key in handbook.ORDER:
            svg = diagrams.render(key)
            root = ET.fromstring(svg)
            self.assertTrue(root.tag.endswith("svg"), key)
            self.assertGreater(len(svg), 900, key)
            self.assertIn("viewBox", svg, key)

    def test_diagram_boxes_stay_inside_the_canvas(self):
        import re
        from k8slab import diagrams
        for key in handbook.ORDER:
            svg = diagrams.render(key)
            height = float(re.search(r'viewBox="0 0 \d+ (\d+)"', svg).group(1))
            for match in re.finditer(
                    r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" '
                    r'height="([\d.]+)"', svg):
                x, y, w, h = (float(g) for g in match.groups())
                self.assertLessEqual(x + w, 981, f"{key}: box runs off the right")
                self.assertLessEqual(y + h, height + 1, f"{key}: box runs off the bottom")

    def test_unknown_key_returns_none(self):
        from k8slab import diagrams
        self.assertIsNone(diagrams.render("no-such-topic"))


class TestCertCoverage(unittest.TestCase):
    def test_every_topic_is_mapped(self):
        unmapped = [k for k in handbook.ORDER if k not in handbook.CERT_MAP]
        self.assertEqual(unmapped, [])

    def test_domain_weights_add_up(self):
        for domains in (handbook.CKA_DOMAINS, handbook.CKAD_DOMAINS):
            self.assertEqual(sum(weight for _, weight in domains.values()), 100)

    def test_every_domain_has_topics(self):
        for exam, domains in handbook.coverage().items():
            for name, info in domains.items():
                self.assertTrue(info["topics"], f"{exam}: {name} has no topics")

    def test_certs_for_returns_badges(self):
        badges = handbook.certs_for("services")
        self.assertEqual({b["exam"] for b in badges}, {"CKA", "CKAD"})
        self.assertTrue(all(b["domain"] and b["weight"] for b in badges))


class TestCompactTopology(unittest.TestCase):
    """A busy cluster must still fit on a screen (wide tiers wrap)."""

    def _busy(self, replicas: int = 24) -> Shell:
        sh = shell()
        sh.run(f"kubectl create deployment web --image=nginx:1.25 --replicas={replicas}")
        sh.run("kubectl expose deploy/web --port=80")
        sh.run("kubectl apply -f manifests/daemonset.yaml")
        reconcile(sh.cluster, 20)
        return sh

    def test_wide_cluster_stays_narrow(self):
        sh = self._busy()
        graph = topology.build(sh.cluster, "default", "logical")
        self.assertGreaterEqual(graph.stats["pods"], 24)
        self.assertLess(graph.width, 1500,
                        f"logical view is {graph.width}px wide -- too wide to read")

    def test_rows_wrap_instead_of_running_off(self):
        sh = self._busy()
        graph = topology.build(sh.cluster, "default", "logical")
        pods = [n for n in graph.nodes if n.kind == "Pod"]
        rows = {round(n.y) for n in pods}
        self.assertGreater(len(rows), 1, "pods should wrap onto several rows")
        for node in graph.nodes:
            self.assertLess(node.x + node.w, graph.width + 1)

    def test_cards_still_do_not_overlap_after_wrapping(self):
        sh = self._busy()
        for view in ("logical", "physical"):
            graph = topology.build(sh.cluster, None, view, include_system=True)
            boxes = [(n.x, n.y, n.x + n.w, n.y + n.h) for n in graph.nodes]
            for i, a in enumerate(boxes):
                for b in boxes[i + 1:]:
                    self.assertFalse(a[0] < b[2] and b[0] < a[2] and
                                     a[1] < b[3] and b[1] < a[3],
                                     f"{view}: overlap {a} {b}")


class TestExportBothViews(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shell = shell()
        cls.shell.run("kubectl create deployment web --image=nginx:1.25 --replicas=3")
        reconcile(cls.shell.cluster, 8)
        cls.url, cls.token = start_web(cls.shell, 8909)
        time.sleep(0.4)

    def test_export_logical_and_physical(self):
        import xml.etree.ElementTree as ET
        for view in ("logical", "physical"):
            with fetch(self.url, self.token, f"/api/export.svg?ns=default&view={view}&system=1", timeout=20) as response:
                body = response.read()
            self.assertEqual(response.status, 200)
            root = ET.fromstring(body)
            self.assertTrue(root.tag.endswith("svg"), view)
            self.assertIn(view.encode(), body)

    def test_diagram_endpoint(self):
        with fetch(self.url, self.token, "/diagram/pods.svg", timeout=20) as r:
            self.assertEqual(r.status, 200)
            self.assertIn(b"<svg", r.read())

    def test_topic_payload_has_diagram_and_certs(self):
        with fetch(self.url, self.token, "/api/topic/services", timeout=20) as r:
            topic = json.loads(r.read())
        self.assertTrue(topic["diagram"].endswith(".svg"))
        self.assertTrue(topic["certs"])

    def test_coverage_endpoint(self):
        with fetch(self.url, self.token, "/api/coverage", timeout=20) as r:
            payload = json.loads(r.read())
        self.assertIn("CKA", payload["coverage"])
        self.assertTrue(payload["not_covered"])


class TestExpectedFailures(unittest.TestCase):
    def test_marker_flags_a_deliberate_failure(self):
        sh = shell()
        script = ("kubectl create deployment ok --image=nginx:1.25\n"
                  "#!expect-error\n"
                  "kubectl apply -f manifests/broken-selector.yaml\n")
        results = dict((cmd, res) for cmd, res in sh.run_script(script))
        broken = results["kubectl apply -f manifests/broken-selector.yaml"]
        self.assertFalse(broken.ok)
        self.assertTrue(broken.expected)
        self.assertFalse(results["kubectl create deployment ok --image=nginx:1.25"]
                         .expected)

    def test_labs_that_fail_on_purpose_are_marked(self):
        for lab in lab_index.LABS:
            if not lab.exists():
                continue
            sh = shell()
            for command, result in sh.run_script(lab.read()):
                if not result.ok:
                    self.assertTrue(result.expected,
                                    f"{lab.file}: `{command}` failed but is not "
                                    "marked with #!expect-error")


class TestPageResolution(unittest.TestCase):
    def test_pages_match_the_pdf_resolution(self):
        if not pages_imported():
            self.skipTest("handbook pages not imported (see handbook/README.md)")
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        import glob as _glob
        pages = sorted(_glob.glob(os.path.join(ROOT, "handbook", "pages", "*.png")))
        self.assertTrue(pages)
        for path in pages:
            with Image.open(path) as image:
                self.assertGreaterEqual(image.width, 1000, os.path.basename(path))
                self.assertGreaterEqual(image.height, 1400, os.path.basename(path))


class TestKubeadm(unittest.TestCase):
    """Cluster lifecycle: the ORDER and the version-skew rules are enforced."""

    def test_control_plane_must_go_first(self):
        sh = shell()
        # nothing to do while the node already matches the control plane
        self.assertIn("already", sh.run("kubeadm upgrade node --node lab-worker-1").out)
        # after the control plane moves, the node must be drained first
        sh.run("kubeadm upgrade apply v1.31.1")
        result = sh.run("kubeadm upgrade node --node lab-worker-1")
        self.assertFalse(result.ok)
        self.assertIn("drain", result.err.lower())

    def test_cannot_skip_a_minor_version(self):
        sh = shell()
        result = sh.run("kubeadm upgrade apply v1.32.0")
        self.assertFalse(result.ok)
        self.assertIn("skip minor versions", result.err)

    def test_full_upgrade_sequence(self):
        from k8slab.admin import node_version
        sh = shell()
        self.assertTrue(sh.run("kubeadm upgrade apply v1.31.1").ok)
        self.assertEqual(sh.cluster.version, "1.31.1")
        control = sh.cluster.get("Node", "", "lab-control-plane")
        self.assertEqual(node_version(control), "1.31.1")

        worker = sh.cluster.get("Node", "", "lab-worker-1")
        self.assertEqual(node_version(worker), "1.30.2")
        self.assertFalse(sh.run("kubeadm upgrade node --node lab-worker-1").ok)
        sh.run("kubectl drain lab-worker-1 --ignore-daemonsets")
        self.assertTrue(sh.run("kubeadm upgrade node --node lab-worker-1").ok)
        self.assertEqual(node_version(worker), "1.31.1")
        sh.run("kubectl uncordon lab-worker-1")
        self.assertFalse(deep_get(worker, "spec.unschedulable"))

    def test_join_adds_a_node_at_the_cluster_version(self):
        from k8slab.admin import node_version
        sh = shell()
        sh.run("kubeadm upgrade apply v1.31.1")
        before = len(sh.cluster.list("Node"))
        result = sh.run("kubeadm join 172.18.0.2:6443 --token abc.def "
                        "--node-name lab-worker-9")
        self.assertTrue(result.ok, result.err)
        self.assertEqual(len(sh.cluster.list("Node")), before + 1)
        self.assertEqual(node_version(sh.cluster.get("Node", "", "lab-worker-9")),
                         "1.31.1")

    def test_join_requires_a_token(self):
        sh = shell()
        self.assertFalse(sh.run("kubeadm join 172.18.0.2:6443 "
                                "--node-name lab-worker-9").ok)

    def test_version_skew_helper(self):
        from k8slab.admin import skew_ok
        self.assertTrue(skew_ok("1.31.1", "1.31.1"))
        self.assertTrue(skew_ok("1.31.1", "1.28.0"))
        self.assertFalse(skew_ok("1.31.1", "1.32.0"))


class TestEtcdBackup(unittest.TestCase):
    ETCD_FLAGS = ("--endpoints=https://127.0.0.1:2379 "
                  "--cacert=/etc/kubernetes/pki/etcd/ca.crt "
                  "--cert=/etc/kubernetes/pki/etcd/server.crt "
                  "--key=/etc/kubernetes/pki/etcd/server.key")

    def test_tls_flags_are_required(self):
        sh = shell()
        result = sh.run("ETCDCTL_API=3 etcdctl snapshot save /tmp/lab-etcd.db")
        self.assertFalse(result.ok)
        for flag_name in ("--endpoints", "--cacert", "--cert", "--key"):
            self.assertIn(flag_name, result.err)

    def test_api_version_two_is_refused(self):
        sh = shell()
        self.assertFalse(sh.run("ETCDCTL_API=2 etcdctl member list").ok)

    def test_save_status_restore_round_trip(self):
        sh = shell()
        sh.run("kubectl create deployment shop --image=nginx:1.25 --replicas=3")
        sh.run("kubectl create configmap shop-config --from-literal=THEME=dark")
        reconcile(sh.cluster, 8)
        self.assertTrue(sh.run(f"ETCDCTL_API=3 etcdctl snapshot save "
                               f"/tmp/lab-etcd.db {self.ETCD_FLAGS}").ok)

        status = sh.run(f"ETCDCTL_API=3 etcdctl snapshot status /tmp/lab-etcd.db "
                        f"{self.ETCD_FLAGS}")
        self.assertTrue(status.ok)
        self.assertIn("TOTAL KEYS", status.out)

        sh.run("kubectl delete deploy shop")
        sh.run("kubectl delete configmap shop-config")
        self.assertIsNone(sh.cluster.get("Deployment", "default", "shop"))

        restore = sh.run(f"ETCDCTL_API=3 etcdctl snapshot restore /tmp/lab-etcd.db "
                         f"--data-dir=/var/lib/etcd-new {self.ETCD_FLAGS}")
        self.assertTrue(restore.ok, restore.err)
        self.assertIsNotNone(sh.cluster.get("Deployment", "default", "shop"))
        self.assertIsNotNone(sh.cluster.get("ConfigMap", "default", "shop-config"))

    def test_restoring_a_missing_snapshot_fails(self):
        sh = shell()
        self.assertFalse(sh.run(f"ETCDCTL_API=3 etcdctl snapshot restore /nope.db "
                                f"{self.ETCD_FLAGS}").ok)


class TestCertificatesAndKubeconfig(unittest.TestCase):
    def test_csr_approve_flow(self):
        sh = shell()
        self.assertTrue(sh.run("kubectl create csr alice --user=alice "
                               "--group=developers").ok)
        csr = sh.cluster.get("CertificateSigningRequest", "", "alice")
        self.assertEqual(deep_get(csr, "status.phase"), "Pending")
        self.assertTrue(sh.run("kubectl certificate approve alice").ok)
        csr = sh.cluster.get("CertificateSigningRequest", "", "alice")
        self.assertEqual(deep_get(csr, "status.phase"), "Approved,Issued")
        self.assertIn("alice", sh.cluster.kubeconfig["users"])

    def test_certificate_alone_grants_nothing(self):
        sh = shell()
        sh.run("kubectl create namespace dev")
        sh.run("kubectl create csr alice --user=alice")
        sh.run("kubectl certificate approve alice")
        sh.run("kubectl config set-context alice-ctx --cluster=lab-cluster "
               "--user=alice --namespace=dev")
        sh.run("sim rbac on")
        sh.run("kubectl config use-context alice-ctx")
        self.assertEqual(sh.cluster.current_user, "alice")
        self.assertFalse(sh.run("kubectl get pods").ok)

        sh.run("kubectl config use-context lab")
        sh.run("kubectl create role pod-reader --verb=get,list,watch "
               "--resource=pods -n dev")
        sh.run("kubectl create rolebinding alice-read --role=pod-reader "
               "--user=alice -n dev")
        sh.run("kubectl config use-context alice-ctx")
        self.assertTrue(sh.run("kubectl get pods").ok)
        self.assertEqual(sh.run("kubectl auth can-i delete pods").out, "no")

    def test_context_switch_changes_identity_and_namespace(self):
        sh = shell()
        sh.run("kubectl create namespace dev")
        sh.run("kubectl config set-credentials bob --token=abc")
        sh.run("kubectl config set-context bob-ctx --cluster=lab-cluster "
               "--user=bob --namespace=dev")
        sh.run("kubectl config use-context bob-ctx")
        self.assertEqual(sh.cluster.current_namespace, "dev")
        self.assertEqual(sh.cluster.current_user, "bob")
        contexts = sh.run("kubectl config get-contexts").out
        self.assertIn("bob-ctx", contexts)
        self.assertTrue(any(line.startswith("*") and "bob-ctx" in line
                            for line in contexts.splitlines()))

    def test_use_unknown_context_fails(self):
        sh = shell()
        self.assertFalse(sh.run("kubectl config use-context nope").ok)

    def test_certificate_expiry_and_renewal(self):
        sh = shell()
        self.assertIn("OK", sh.run("kubeadm certs check-expiration").out)
        sh.run("sim age-certs 350")
        self.assertIn("EXPIRING SOON", sh.run("kubeadm certs check-expiration").out)
        sh.run("kubeadm certs renew all")
        self.assertNotIn("EXPIRING SOON", sh.run("kubeadm certs check-expiration").out)


class TestNetworkPolicyEnforcement(unittest.TestCase):
    def _two_apps(self) -> Shell:
        sh = shell()
        sh.run("kubectl apply -f manifests/two-apps.yaml")
        sh.run("kubectl run client --image=busybox:1.36 --labels=tier=frontend")
        sh.run("kubectl run outsider --image=busybox:1.36 --labels=tier=other")
        reconcile(sh.cluster, 10)
        return sh

    def test_open_by_default(self):
        sh = self._two_apps()
        self.assertTrue(sh.run("kubectl exec outsider -- curl http://api:8080").ok)

    def test_policy_denies_everyone_else(self):
        sh = self._two_apps()
        sh.run("kubectl apply -f manifests/networkpolicy-lab28.yaml")
        reconcile(sh.cluster, 3)
        allowed = sh.run("kubectl exec client -- curl http://api:8080")
        denied = sh.run("kubectl exec outsider -- curl http://api:8080")
        self.assertTrue(allowed.ok, allowed.err)
        self.assertFalse(denied.ok)
        self.assertIn("NetworkPolicy", denied.err)
        self.assertIn("api-allow-from-client", denied.err)

    def test_egress_policy_is_enforced_too(self):
        sh = self._two_apps()
        sh.run("kubectl apply -f manifests/networkpolicy-egress.yaml")
        reconcile(sh.cluster, 3)
        self.assertFalse(sh.run("kubectl exec client -- curl http://web:80").ok)
        sh.run("kubectl apply -f manifests/networkpolicy-lab28.yaml")
        reconcile(sh.cluster, 3)
        self.assertTrue(sh.run("kubectl exec client -- curl http://api:8080").ok)

    def test_deleting_policies_reopens_the_network(self):
        sh = self._two_apps()
        sh.run("kubectl apply -f manifests/networkpolicy-lab28.yaml")
        reconcile(sh.cluster, 3)
        self.assertFalse(sh.run("kubectl exec outsider -- curl http://api:8080").ok)
        sh.run("kubectl delete netpol --all")
        reconcile(sh.cluster, 3)
        self.assertTrue(sh.run("kubectl exec outsider -- curl http://api:8080").ok)

    def test_connectivity_matrix(self):
        from k8slab import netpolicy
        sh = self._two_apps()
        sh.run("kubectl apply -f manifests/networkpolicy-lab28.yaml")
        reconcile(sh.cluster, 3)
        names, grid = netpolicy.matrix(sh.cluster, "default", 8080)
        self.assertTrue(names)
        client = names.index("client")
        api = next(i for i, n in enumerate(names) if n.startswith("api-"))
        outsider = names.index("outsider")
        self.assertTrue(grid[client][api].allowed)
        self.assertFalse(grid[outsider][api].allowed)

    def test_selector_semantics(self):
        from k8slab import netpolicy
        sh = self._two_apps()
        api = next(p for p in sh.cluster.list("Pod", "default")
                   if str(deep_get(p, "metadata.name")).startswith("api-"))
        self.assertEqual(netpolicy.isolated(sh.cluster, api),
                         {"Ingress": [], "Egress": []})
        sh.run("kubectl apply -f manifests/networkpolicy-lab28.yaml")
        self.assertEqual(netpolicy.isolated(sh.cluster, api)["Ingress"],
                         ["api-allow-from-client"])


class TestImagesAndRegistry(unittest.TestCase):
    def test_locally_built_image_cannot_be_pulled(self):
        sh = shell()
        sh.run("docker build -t myapp:1.0 .")
        sh.run("kubectl run local-only --image=myapp:1.0")
        reconcile(sh.cluster, 8)
        pod = sh.cluster.get("Pod", "default", "local-only")
        self.assertEqual(deep_get(pod, "status.reason"), "ImagePullBackOff")

    def test_pushed_public_image_runs(self):
        sh = shell()
        sh.run("docker build -t myapp:1.0 .")
        sh.run("docker tag myapp:1.0 registry.lab/public/myapp:1.0")
        sh.run("docker push registry.lab/public/myapp:1.0")
        sh.run("kubectl run public-app --image=registry.lab/public/myapp:1.0")
        reconcile(sh.cluster, 10)
        pod = sh.cluster.get("Pod", "default", "public-app")
        self.assertEqual(deep_get(pod, "status.phase"), "Running")

    def test_private_image_needs_a_pull_secret(self):
        sh = shell()
        sh.run("docker build -t myapp:1.0 .")
        sh.run("docker tag myapp:1.0 registry.lab/private/myapp:1.0")
        sh.run("docker push registry.lab/private/myapp:1.0")
        sh.run("kubectl run needs-auth --image=registry.lab/private/myapp:1.0")
        reconcile(sh.cluster, 8)
        pod = sh.cluster.get("Pod", "default", "needs-auth")
        self.assertEqual(deep_get(pod, "status.reason"), "ImagePullBackOff")
        self.assertIn("pull access denied", deep_get(pod, "status.message", ""))

        sh.run("kubectl create secret docker-registry regcred "
               "--docker-server=registry.lab --docker-username=dev "
               "--docker-password=s3cr3t")
        secret = sh.cluster.get("Secret", "default", "regcred")
        self.assertEqual(secret.get("type"), "kubernetes.io/dockerconfigjson")
        sh.run("kubectl apply -f manifests/pod-private-image.yaml")
        reconcile(sh.cluster, 10)
        started = sh.cluster.get("Pod", "default", "private-app")
        self.assertEqual(deep_get(started, "status.phase"), "Running")


class TestShippedTreeIsClean(unittest.TestCase):
    """Nothing from a previous run may travel with the project."""

    def test_no_progress_file_is_shipped(self):
        stray = os.path.join(ROOT, "lab-progress.json")
        self.assertFalse(os.path.exists(stray),
                         "lab-progress.json is in the project tree -- a fresh "
                         "install would start with topics already ticked")

    def test_no_scratch_files_are_shipped(self):
        for name in (".webui-buffer.yaml", ".editor-buffer.yaml",
                     "cluster-state.json"):
            self.assertFalse(os.path.exists(os.path.join(ROOT, name)), name)


class TestEntryPoint(unittest.TestCase):
    def test_cli_module_imports_and_lists_labs(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "k8s_lab_entry", os.path.join(ROOT, "k8s_lab.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.main(["--list-labs"]), 0)

    def test_ascii_topology(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "k8s_lab_entry2", os.path.join(ROOT, "k8s_lab.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sh = shell()
        sh.run("kubectl create deployment web --image=nginx:1.25 --replicas=2")
        reconcile(sh.cluster, 6)
        text = module._ascii_topology(topology.build(sh.cluster, "default"))
        self.assertIn("Deployment", text)
        self.assertIn("Pod", text)

    def test_gui_module_is_syntactically_valid(self):
        import ast
        with open(os.path.join(ROOT, "k8slab", "gui.py"), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        classes = {node.name for node in ast.walk(tree)
                   if isinstance(node, ast.ClassDef)}
        self.assertIn("LabApp", classes)
        self.assertIn("DockerPanel", classes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
