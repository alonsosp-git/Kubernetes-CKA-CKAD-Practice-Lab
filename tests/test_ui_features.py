"""Tests for the UI-facing features: colours, manifests, dictionary, exports.

Kept in its own module so `test_lab.py` stays about the simulator itself.

    python -m unittest discover -s tests -t . -v
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
import unittest
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from k8slab import (dictionary, export, handbook, labs as lab_index,      # noqa: E402
                    miniyaml, topology)
from k8slab.apiserver import ApiError, apply                              # noqa: E402
from k8slab.controllers import reconcile                                  # noqa: E402
from k8slab.kubectl import Shell                                          # noqa: E402
from k8slab.model import deep_get                                         # noqa: E402


# keep every test's progress file in a scratch dir -- a shipped lab-progress.json
# would make a fresh install look like topics were already covered
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


# ---------------------------------------------------------------------------
class TestSectionColours(unittest.TestCase):
    """Every section gets its own hue family, and nothing is yellow."""

    @staticmethod
    def _is_yellow(hex_colour: str) -> bool:
        red = int(hex_colour[1:3], 16)
        green = int(hex_colour[3:5], 16)
        blue = int(hex_colour[5:7], 16)
        return red > 175 and green > 165 and blue < 125

    def test_every_kind_has_a_section_and_a_tone(self):
        for kind, (section, tone) in topology.KIND_STYLE.items():
            self.assertTrue(section, kind)
            self.assertRegex(tone, r"^#[0-9a-fA-F]{6}$", kind)

    def test_section_colours_are_distinct(self):
        tones = list(topology.SECTION_COLORS.values())
        self.assertEqual(len(tones), len(set(tones)))

    def test_kind_tones_are_distinct(self):
        tones = [tone for _, tone in topology.KIND_STYLE.values()]
        self.assertEqual(len(tones), len(set(tones)))

    def test_nothing_is_yellow(self):
        for kind, (_, tone) in topology.KIND_STYLE.items():
            self.assertFalse(self._is_yellow(tone), f"{kind} is yellow: {tone}")
        for section, tone in topology.SECTION_COLORS.items():
            self.assertFalse(self._is_yellow(tone), f"{section} is yellow: {tone}")

    def test_related_kinds_share_a_family(self):
        for kind in ("Pod", "Deployment", "ReplicaSet", "StatefulSet", "DaemonSet"):
            self.assertEqual(topology.section_of(kind), "Workloads", kind)
        for kind in ("Service", "Ingress", "NetworkPolicy"):
            self.assertEqual(topology.section_of(kind), "Networking", kind)
        for kind in ("PersistentVolume", "PersistentVolumeClaim", "StorageClass"):
            self.assertEqual(topology.section_of(kind), "Storage", kind)

    def test_edges_are_tinted(self):
        sh = shell()
        sh.run("kubectl apply -f manifests/two-apps.yaml")
        sh.run("kubectl apply -f manifests/ingress.yaml")
        reconcile(sh.cluster, 8)
        graph = topology.build(sh.cluster, "default")
        self.assertTrue(graph.edges)
        for edge in graph.edges:
            self.assertRegex(edge.colour, r"^#[0-9a-fA-F]{6}$")
        svg = export.to_svg(graph, "colours")
        self.assertIn(topology.colour_of("Pod"), svg)
        self.assertIn(topology.colour_of("Service"), svg)

    def test_legend_covers_every_kind(self):
        detail = topology.legend_detail()
        kinds = {entry["kind"] for group in detail for entry in group["kinds"]}
        self.assertEqual(kinds, set(topology.KIND_STYLE))


# ---------------------------------------------------------------------------
class TestManifests(unittest.TestCase):
    """Each topic has a labelled, downloadable, applyable manifest."""

    def test_filenames_carry_topic_lab_and_page(self):
        for key in handbook.ORDER:
            topic = handbook.TOPICS[key]
            name = handbook.manifest_filename(topic)
            self.assertTrue(name.endswith(".yaml"), key)
            self.assertIn(topic.key, name)
            self.assertIn(f"p{topic.pages[0]}", name)
            if topic.lab_number:
                self.assertIn(f"lab{topic.lab_number:02d}", name)

    def test_header_labels_the_exercise(self):
        body = handbook.manifest_for(handbook.TOPICS["deployments"])
        for needle in ("Topic:", "Section:", "Lab:", "Handbook: page 6", "Key:"):
            self.assertIn(needle, body)

    def test_every_manifest_parses_and_applies(self):
        for key in handbook.ORDER:
            topic = handbook.TOPICS[key]
            docs = miniyaml.load_all(handbook.manifest_for(topic))
            sh = shell()
            sh.run("kubectl create namespace dev")
            for doc in docs:
                try:
                    apply(sh.cluster, doc)
                except ApiError as exc:
                    self.fail(f"{key}: generated manifest rejected: {exc}")

    def test_topics_with_yaml_produce_objects(self):
        produced = [key for key in handbook.ORDER
                    if miniyaml.load_all(handbook.manifest_for(handbook.TOPICS[key]))]
        self.assertGreaterEqual(len(produced), 20)

    def test_applying_a_command_script_is_refused(self):
        sh = shell()
        path = os.path.join(ROOT, ".test-script-buffer.yaml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(lab_index.by_file("04-deployments.kubectl").read())
        try:
            self.assertFalse(sh.run("kubectl apply -f .test-script-buffer.yaml").ok)
        finally:
            os.remove(path)

    def test_comment_only_manifest_explains_itself(self):
        sh = shell()
        path = os.path.join(ROOT, ".test-empty-buffer.yaml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(handbook.manifest_for(handbook.TOPICS["architecture"]))
        try:
            result = sh.run("kubectl apply -f .test-empty-buffer.yaml")
            self.assertTrue(result.ok)
            self.assertIn("comment-only", result.out)
        finally:
            os.remove(path)


# ---------------------------------------------------------------------------
class TestObjectDictionary(unittest.TestCase):
    def _cluster(self) -> Shell:
        sh = shell()
        sh.run("kubectl create deployment web --image=nginx:1.25 --replicas=2")
        sh.run("kubectl expose deploy/web --port=80")
        sh.run("kubectl create configmap app --from-literal=A=1")
        reconcile(sh.cluster, 10)
        return sh

    def test_entries_are_in_creation_order(self):
        entries = dictionary.build(self._cluster().cluster, "default")
        self.assertEqual([e["order"] for e in entries],
                         list(range(1, len(entries) + 1)))
        ticks = [e["tick"] for e in entries]
        self.assertEqual(ticks, sorted(ticks))

    def test_every_entry_explains_itself(self):
        for entry in dictionary.build(self._cluster().cluster, "default"):
            self.assertTrue(entry["purpose"], entry["kind"])
            self.assertTrue(entry["detail"], entry["kind"])
            self.assertIn(entry["origin"], ("you", "controller", "cluster"))

    def test_provenance_separates_you_from_controllers(self):
        entries = {e["kind"]: e for e in
                   dictionary.build(self._cluster().cluster, "default")}
        self.assertEqual(entries["Deployment"]["origin"], "you")
        self.assertIn("kubectl create deployment",
                      entries["Deployment"]["origin_detail"])
        self.assertEqual(entries["ReplicaSet"]["origin"], "controller")
        self.assertIn("Deployment/web", entries["ReplicaSet"]["origin_detail"])
        self.assertEqual(entries["Pod"]["origin"], "controller")

    def test_details_read_the_real_spec(self):
        entries = {e["kind"]: e for e in
                   dictionary.build(self._cluster().cluster, "default")}
        self.assertIn("nginx:1.25", entries["Deployment"]["detail"])
        self.assertIn("2 ready endpoint", entries["Service"]["detail"])
        self.assertIn("1 key", entries["ConfigMap"]["detail"])

    def test_entries_link_back_to_the_handbook(self):
        for entry in dictionary.build(self._cluster().cluster, "default"):
            if entry["kind"] in ("Deployment", "Pod", "Service", "ConfigMap"):
                self.assertTrue(entry["topic_title"], entry["kind"])
                self.assertTrue(entry["page"], entry["kind"])

    def test_text_export(self):
        sh = self._cluster()
        text = dictionary.to_text(sh.cluster, dictionary.build(sh.cluster, "default"))
        self.assertIn("what it is:", text)
        self.assertIn("what it does:", text)
        self.assertIn("handbook:", text)


# ---------------------------------------------------------------------------
class TestInteractiveExport(unittest.TestCase):
    def _graph(self):
        sh = shell()
        sh.run("kubectl create deployment web --image=nginx:1.25 --replicas=3")
        reconcile(sh.cluster, 8)
        return topology.build(sh.cluster, "default")

    def test_html_is_self_contained_and_interactive(self):
        html = export.to_interactive_html(self._graph(), "test view")
        for needle in ("<svg", "dragNode", "function restore()", "function fit()",
                       "requestFullscreen", "function save()", "wheel"):
            self.assertIn(needle, html)
        self.assertNotIn("<script src", html)
        self.assertNotIn("http://", html.split("<svg")[0])

    def test_metadata_matches_the_nodes(self):
        graph = self._graph()
        html = export.to_interactive_html(graph, "t")
        meta = json.loads(re.search(r"const META=(\[.*?\]);", html, re.S).group(1))
        self.assertEqual(len(meta), len(graph.nodes))
        self.assertEqual(meta[0]["kind"], graph.nodes[0].kind)


# ---------------------------------------------------------------------------
class TestWebFeatures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shell = shell()
        cls.url, cls.token = start_web(cls.shell, 8911)
        time.sleep(0.4)

    def get(self, path):
        with fetch(self.url, self.token, path, timeout=30) as response:
            return response.read()

    def post(self, path, payload):
        with fetch(self.url, self.token, path, payload, timeout=90) as response:
            return json.loads(response.read())

    def test_page_has_the_new_controls(self):
        html = self.get("/").decode()
        for needle in ("runAllLabs(", "popOut(", "setMode('manifest')",
                       "downloadYaml(", "loadDict(", "doExport(",
                       'id="legend"', 'id="popout"', "/api/manifests.zip",
                       "/api/log.txt", "/api/labs-bundle.zip"):
            self.assertIn(needle, html, needle)

    def test_only_one_tab_body_is_visible_at_a_time(self):
        """Every panel was rendering at once -- CSS must hide all but one."""
        html = self.get("/").decode()
        self.assertIn(".tabbody{", html)
        self.assertIn("display:none", html.split(".tabbody{")[1].split("}")[0])
        self.assertIn(".tabbody.on{display:block}", html)
        self.assertEqual(html.count('class="tabbody on"'), 1)
        bodies = re.findall(r'<div class="tabbody(?: on)?" id="t-(\w+)"', html)
        self.assertEqual(len(bodies), len(set(bodies)))
        for name in bodies:                       # every body has a tab button
            self.assertIn(f'data-tab="{name}"', html)
        for name in re.findall(r'data-tab="(\w+)"', html):
            self.assertIn(f'id="t-{name}"', html)

    def test_no_rule_can_outrank_the_hide_rule(self):
        """An ID rule setting display would leak a panel onto every tab."""
        html = self.get("/").decode()
        css = html.split("<style>", 1)[1].split("</style>", 1)[0]
        for rule in re.finditer(r"(#t-[\w-]+)((?:\.\w+)?)\s*\{([^}]*)\}", css):
            selector, modifier, body = rule.groups()
            if "display" in body:
                self.assertEqual(modifier, ".on",
                                 f"{selector}{modifier} sets display outside .on -- "
                                 "it will show under every tab")

    def test_two_download_buttons_at_the_top(self):
        html = self.get("/").decode()
        self.assertIn("/api/labs-bundle.zip?flavour=kubernetes", html)
        self.assertIn("/api/labs-bundle.zip?flavour=minikube", html)
        self.assertIn("(kubectl)", html)
        self.assertIn("(minikube)", html)
        self.assertNotIn('data-tab="real"', html)      # no buried sub-panel

    def test_object_dictionary_keeps_itself_current(self):
        """It used to load once and then freeze."""
        html = self.get("/").decode()
        self.assertIn("loadDict()", html.split("setInterval(")[1][:120])
        self.assertIn("function loadDict(force)", html)
        self.assertIn("loadDict(true)", html)           # explicit refresh
        self.assertIn('id="dictscope"', html)           # says what it is showing

    def test_dictionary_follows_the_cluster(self):
        before = json.loads(self.get("/api/dictionary?ns=default"))["objects"]
        self.post("/api/run", {"cmd": "kubectl create configmap dict-probe "
                                      "--from-literal=a=b"})
        self.post("/api/tick", {"n": 3})
        after = json.loads(self.get("/api/dictionary?ns=default"))["objects"]
        self.assertGreater(len(after), len(before))
        self.assertTrue(any(o["name"] == "dict-probe" for o in after))

    def test_progress_starts_empty_and_can_be_cleared(self):
        html = self.get("/").decode()
        self.assertIn("clearProgress(", html)
        self.post("/api/progress", {"done": ["pods"]})
        self.assertEqual(json.loads(self.get("/api/progress"))["done"], ["pods"])
        self.post("/api/reset", {"scope": "progress"})
        self.assertEqual(json.loads(self.get("/api/progress"))["done"], [])

    def test_preview_endpoint_still_serves_each_file(self):
        for name, needle in (("run-all.sh", "preflight"),
                             ("env.sh", 'KUBECTL="${KUBECTL:-kubectl}"'),
                             ("lib.sh", "expect_fail()"),
                             ("05-services.kubectl", "banner \"05 - Services")):
            body = self.get(f"/api/preview-command?lab={name}").decode()
            self.assertIn(needle, body, name)

    def test_sidebar_gets_lab_numbers(self):
        payload = json.loads(self.get("/api/handbook"))
        topics = [t for section in payload["sections"] for t in section["topics"]]
        self.assertTrue(all("lab" in t and "page" in t for t in topics))
        self.assertEqual(next(t["lab"] for t in topics if t["key"] == "deployments"), 4)

    def test_topic_payload_carries_manifest_and_lab(self):
        topic = json.loads(self.get("/api/topic/certificates"))
        self.assertEqual(topic["lab_number"], 27)
        self.assertEqual(topic["section"], "Cluster administration")
        self.assertIn("lab27", topic["manifest_name"])
        self.assertIn("Topic:", topic["manifest"])

    def test_manifest_download_headers(self):
        with fetch(self.url, self.token, "/api/manifest/pods.yaml",
                                    timeout=20) as response:
            self.assertIn("attachment", response.headers["Content-Disposition"])
            self.assertIn(b"Topic:", response.read())

    def _bundle(self, flavour: str):
        import io
        import zipfile
        body = self.get(f"/api/labs-bundle.zip?flavour={flavour}")
        self.assertEqual(body[:2], b"PK")
        return zipfile.ZipFile(io.BytesIO(body))

    def test_both_flavours_download(self):
        from k8slab import portable
        for flavour in portable.FLAVOURS:
            archive = self._bundle(flavour)
            root = f"k8s-labs-{flavour}"
            names = archive.namelist()
            self.assertIn(f"{root}/START-HERE.md", names)
            self.assertIn(f"{root}/{portable.SH_DIR}/run-all.sh", names)
            self.assertIn(f"{root}/{portable.SH_DIR}/env.sh", names)
            self.assertEqual(
                sum(1 for n in names
                    if n.startswith(f"{root}/{portable.SH_DIR}/labs/")),
                len(lab_index.available()), flavour)
            self.assertEqual(
                sum(1 for n in names if n.startswith(f"{root}/{portable.YAML_DIR}/")
                    and n.endswith(".yaml")), len(handbook.ORDER), flavour)
            self.assertEqual(
                sum(1 for n in names
                    if n.startswith(f"{root}/{portable.KUBECTL_DIR}/")
                    and n.endswith(".kubectl")),
                len(lab_index.available()), flavour)

    def test_folders_use_the_requested_names(self):
        from k8slab import portable
        self.assertEqual(portable.SH_DIR, "Lab .sh scripts")
        self.assertEqual(portable.KUBECTL_DIR, "Lab .kubectl scripts")
        self.assertEqual(portable.YAML_DIR, "Lab .yaml scripts")
        names = self._bundle("kubernetes").namelist()
        for folder in (portable.SH_DIR, portable.KUBECTL_DIR, portable.YAML_DIR):
            self.assertTrue(any(f"/{folder}/" in n for n in names), folder)

    def test_environment_setup_ships_in_both_flavours(self):
        from k8slab import portable
        for flavour in portable.FLAVOURS:
            archive = self._bundle(flavour)
            path = (f"k8s-labs-{flavour}/{portable.SH_DIR}/"
                    f"{portable.ENV_SETUP_NAME}")
            self.assertIn(path, archive.namelist(), flavour)
            setup = archive.read(path).decode()
            for needle in ("dl.k8s.io", "get-helm-3", "install_kustomize",
                           "--check", "--yes", "aliases.sh", "BINDIR",
                           "docs.docker.com"):
                self.assertIn(needle, setup, f"{flavour}: {needle}")
            if flavour == "minikube":
                self.assertIn("install_minikube", setup)
                self.assertIn("storage.googleapis.com/minikube", setup)

    def test_environment_setup_writes_working_aliases(self):
        from k8slab import portable
        generic = self._bundle("kubernetes").read(
            f"k8s-labs-kubernetes/{portable.SH_DIR}/"
            f"{portable.ENV_SETUP_NAME}").decode()
        self.assertIn("alias k='kubectl'", generic)
        self.assertIn("alias kgp='kubectl get pods'", generic)
        self.assertIn("alias h='helm'", generic)

        mini = self._bundle("minikube").read(
            f"k8s-labs-minikube/{portable.SH_DIR}/"
            f"{portable.ENV_SETUP_NAME}").decode()
        # on minikube, kubectl itself is aliased to the profile
        self.assertIn("alias kubectl='$KUBECTL_ALIAS'", mini)
        self.assertIn('KUBECTL_ALIAS="$MINIKUBE -p $PROFILE kubectl --"', mini)
        for alias in ("alias k=", "alias kgp=", "alias kaf=", "alias helm=",
                      "alias mk=", "alias mkdocker=", "alias mkip="):
            self.assertIn(alias, mini, alias)

    def test_environment_setup_check_mode_installs_nothing(self):
        import shutil
        import subprocess
        import tempfile
        from k8slab import portable
        if not shutil.which("bash"):
            self.skipTest("bash not available")
        with tempfile.TemporaryDirectory() as folder:
            self._bundle("minikube").extractall(folder)
            root = os.path.join(folder, "k8s-labs-minikube", portable.SH_DIR)
            result = subprocess.run(
                ["bash", portable.ENV_SETUP_NAME, "--check"], cwd=root,
                capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("environment check", result.stdout)
            self.assertIn("nothing was installed", result.stdout)
            self.assertFalse(os.path.exists(os.path.join(root, "aliases.sh")))

    def test_minikube_bundle_sets_itself_up(self):
        from k8slab import portable
        archive = self._bundle("minikube")
        root = "k8s-labs-minikube"
        setup = archive.read(
            f"{root}/{portable.SH_DIR}/{portable.CLUSTER_SETUP_NAME}").decode()
        for needle in ("$MINIKUBE start", '-p "$PROFILE"', "--nodes",
                       "addons enable", "update-context", "aliases.sh",
                       "docker-env"):
            self.assertIn(needle, setup, needle)
        # the aliases it writes
        for alias in ("alias kubectl=", "alias k=", "alias kgp=", "alias helm=",
                      "alias h=", "alias mk=", "alias kaf="):
            self.assertIn(alias, setup, alias)
        env = archive.read(f"{root}/{portable.SH_DIR}/env.sh").decode()
        self.assertIn('KUBECTL="${KUBECTL:-minikube kubectl --}"', env)
        for name in ("PROFILE", "NODES", "CPUS", "MEMORY", "ADDONS"):
            self.assertIn(f'{name}="${{{name}:-', env)
        runner = archive.read(f"{root}/{portable.SH_DIR}/run-all.sh").decode()
        self.assertIn(portable.CLUSTER_SETUP_NAME, runner)

    def test_generic_bundle_has_no_minikube_setup(self):
        from k8slab import portable
        names = self._bundle("kubernetes").namelist()
        self.assertFalse(any(portable.CLUSTER_SETUP_NAME in n for n in names))
        env = self._bundle("kubernetes").read(
            f"k8s-labs-kubernetes/{portable.SH_DIR}/env.sh").decode()
        self.assertIn('KUBECTL="${KUBECTL:-kubectl}"', env)

    def test_minikube_does_node_changes_for_real(self):
        from k8slab import portable
        line, note = portable.translate_command("sim node lab-worker-4 add",
                                                "minikube")
        self.assertIn("node add", line)
        self.assertIn("guard_destructive", line)
        line, _ = portable.translate_command("sim node lab-worker-4 add",
                                             "kubernetes")
        self.assertEqual(line, "")

    def test_every_script_in_both_flavours_is_valid_bash(self):
        import shutil
        import subprocess
        import tempfile
        from k8slab import portable
        if not shutil.which("bash"):
            self.skipTest("bash not available")
        for flavour in portable.FLAVOURS:
            with tempfile.TemporaryDirectory() as folder:
                self._bundle(flavour).extractall(folder)
                root = os.path.join(folder, f"k8s-labs-{flavour}", portable.SH_DIR)
                scripts = [os.path.join(root, n) for n in os.listdir(root)
                           if n.endswith(".sh")]
                scripts += [os.path.join(root, "labs", n)
                            for n in os.listdir(os.path.join(root, "labs"))]
                for path in scripts:
                    result = subprocess.run(["bash", "-n", path],
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0,
                                     f"{flavour}/{os.path.basename(path)}: "
                                     f"{result.stderr}")

    def test_manifests_zip_contains_everything(self):
        import io
        import zipfile
        body = self.get("/api/manifests.zip")
        self.assertEqual(body[:2], b"PK")
        archive = zipfile.ZipFile(io.BytesIO(body))
        names = archive.namelist()
        self.assertIn("INDEX.txt", names)
        self.assertEqual(sum(1 for n in names if n.startswith("manifests/")),
                         len(handbook.ORDER))
        self.assertEqual(sum(1 for n in names if n.startswith("scripts/")),
                         len(lab_index.available()))

    def test_log_exports(self):
        self.post("/api/run", {"cmd": "kubectl get nodes"})
        text = self.get("/api/log.txt").decode()
        self.assertIn("session transcript", text)
        self.assertIn("kubectl get nodes", text)
        entries = json.loads(self.get("/api/log.json"))
        for key in ("cmd", "ok", "out", "tick", "at", "elapsed"):
            self.assertIn(key, entries[-1])

    def test_dictionary_endpoints(self):
        self.post("/api/run", {"cmd": "kubectl create deployment d1 --image=nginx:1.25"})
        self.post("/api/tick", {"n": 6})
        payload = json.loads(self.get("/api/dictionary?ns=default"))
        self.assertTrue(any(o["kind"] == "Deployment" for o in payload["objects"]))
        self.assertIn(b"what it does:", self.get("/api/dictionary.txt"))

    def test_interactive_and_svg_exports(self):
        html = self.get("/api/export.html?ns=default&view=logical").decode()
        self.assertIn("restore()", html)
        for view in ("logical", "physical"):
            svg = self.get(f"/api/export.svg?ns=default&view={view}&system=1")
            self.assertIn(b"<svg", svg)

    def test_legend_endpoint(self):
        payload = json.loads(self.get("/api/legend"))
        self.assertTrue(payload["sections"])
        self.assertIn("colour", payload["sections"][0])
        self.assertIn("kinds", payload["sections"][0])

    def test_run_all_starts_and_stops(self):
        self.assertTrue(self.post("/api/run-all", {"pause": 0.0})["ok"])
        deadline, progressed = time.time() + 25, False
        while time.time() < deadline:
            runner = json.loads(self.get("/api/run-all/status"))
            if runner["index"] >= 2:
                progressed = True
                break
            time.sleep(0.4)
        self.assertTrue(progressed, "the runner never reached the second lab")
        self.post("/api/run-all", {"stop": True})
        for _ in range(30):
            if not json.loads(self.get("/api/run-all/status"))["active"]:
                break
            time.sleep(0.5)
        self.assertFalse(json.loads(self.get("/api/run-all/status"))["active"])


# ---------------------------------------------------------------------------
class TestPortableExport(unittest.TestCase):
    """The labs, translated into shell scripts for a real cluster."""

    @classmethod
    def setUpClass(cls):
        import io
        import zipfile
        from k8slab import portable
        cls.portable = portable
        cls.archive = zipfile.ZipFile(io.BytesIO(portable.build_bundle()))
        cls.names = cls.archive.namelist()

    def read(self, name: str) -> str:
        return self.archive.read(f"k8s-lab-commands/{name}").decode()

    def test_lab_images_all_pull(self):
        """A lab's own image must never look like a broken image."""
        from k8slab.admin import image_known
        sh = shell()
        for image in ("nginx:1.25", "busybox:1.36", "postgres:16",
                      "prom/node-exporter:v1.8.1", "fluent/fluent-bit:3.0",
                      "hashicorp/http-echo:1.0",
                      "registry.k8s.io/ingress-nginx/controller:v1.11.1",
                      "lab.example.com/backup-operator:v1.2.0"):
            exists, private = image_known(sh.cluster, image)
            self.assertTrue(exists, f"{image} would fail to pull")
            self.assertFalse(private, image)

    def test_bundle_layout(self):
        for needed in ("README.md", "env.sh", "lib.sh", "run-all.sh", "INDEX.txt"):
            self.assertIn(f"k8s-lab-commands/{needed}", self.names, needed)
        scripts = [n for n in self.names if n.endswith(".sh") and "/labs/" in n]
        self.assertEqual(len(scripts), len(lab_index.available()))
        self.assertTrue(any(n.endswith("manifests/deployment.yaml")
                            for n in self.names))

    def test_every_script_is_valid_bash(self):
        import shutil
        import subprocess
        import tempfile
        if not shutil.which("bash"):
            self.skipTest("bash not available")
        with tempfile.TemporaryDirectory() as folder:
            self.archive.extractall(folder)
            root = os.path.join(folder, "k8s-lab-commands")
            for name in sorted(os.listdir(os.path.join(root, "labs"))) + \
                    ["../run-all.sh", "../env.sh", "../lib.sh"]:
                path = os.path.join(root, "labs", name)
                result = subprocess.run(["bash", "-n", path],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0,
                                 f"{name}: {result.stderr}")

    def test_variables_are_documented(self):
        env = self.read("env.sh")
        for name, default, description in self.portable.VARIABLES:
            self.assertIn(f'{name}="${{{name}:-{default}}}"', env, name)
            self.assertIn(description.split(".")[0][:40], env, name)

    def test_overrides_are_applied(self):
        import io
        import zipfile
        body = self.portable.build_bundle({"NS": "my-lab", "IMAGE": "nginx:1.27"})
        env = zipfile.ZipFile(io.BytesIO(body)).read(
            "k8s-lab-commands/env.sh").decode()
        self.assertIn('NS="${NS:-my-lab}"', env)
        self.assertIn('IMAGE="${IMAGE:-nginx:1.27}"', env)

    def test_kubectl_calls_use_the_variable(self):
        script = self.read("labs/04-deployments.sh")
        self.assertIn("step $KUBECTL apply -f", script)
        self.assertIn("$MANIFESTS/deployment.yaml", script)
        self.assertNotIn("\nkubectl ", script)

    def test_simulator_commands_become_explained_comments(self):
        script = self.read("labs/16-autoscaling.sh")
        self.assertIn("# skipped: sim load", script)
        self.assertIn("generate traffic", script)
        self.assertNotIn("step sim ", script)

    def test_node_level_commands_are_guarded(self):
        etcd = self.read("labs/26-etcd-backup.sh")
        self.assertIn("guard_node", etcd)
        images = self.read("labs/29-images-registry.sh")
        self.assertIn("guard_docker", images)
        kubeadm = self.read("labs/25-kubeadm-upgrade.sh")
        self.assertIn("guard_node", kubeadm)

    def test_expected_failures_use_expect_fail(self):
        script = self.read("labs/04-deployments.sh")
        self.assertIn("expect_fail", script)
        self.assertIn("SUPPOSED to fail", script)

    def test_lib_provides_aliases_and_guards(self):
        lib = self.read("lib.sh")
        for helper in ("k()", "kgp()", "kaf()", "kns()", "h()", "wait_ready",
                       "preflight", "guard_node", "guard_docker",
                       "guard_destructive", "expect_fail", "step"):
            self.assertIn(helper, lib, helper)

    def test_readme_explains_every_variable(self):
        readme = self.read("README.md")
        self.assertIn("minikube start", readme)
        for name, _, _ in self.portable.VARIABLES:
            self.assertIn(f"`{name}`", readme, name)

    def test_it_actually_runs_against_a_stub_kubectl(self):
        import shutil
        import stat
        import subprocess
        import tempfile
        if not shutil.which("bash"):
            self.skipTest("bash not available")
        with tempfile.TemporaryDirectory() as folder:
            self.archive.extractall(folder)
            root = os.path.join(folder, "k8s-lab-commands")
            binaries = os.path.join(folder, "bin")
            os.makedirs(binaries)
            for name in ("kubectl", "helm", "kustomize"):
                path = os.path.join(binaries, name)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("#!/bin/sh\necho \"[%s] $*\"\nexit 0\n" % name)
                os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
            env = dict(os.environ, PATH=binaries + os.pathsep + os.environ["PATH"])
            result = subprocess.run(["bash", "run-all.sh", "4", "5"], cwd=root,
                                    capture_output=True, text=True, env=env,
                                    timeout=120)
            self.assertIn("Deployments", result.stdout)
            self.assertIn("[kubectl] apply -f", result.stdout)
            self.assertIn("all done", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
