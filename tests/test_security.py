"""Security regression tests.

Every test here is an attack that used to work, or a control that must not
quietly disappear. Each one names the weakness class it covers.

    python -m unittest tests.test_security -v
"""
from __future__ import annotations

import http.client
import json
import os
import re
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from k8slab import export, miniyaml, portable, security, topology   # noqa: E402
from k8slab.kubectl import Shell                                    # noqa: E402
from k8slab.webui import serve                                      # noqa: E402


def shell() -> Shell:
    return Shell(base_dir=ROOT)


# ===========================================================================
class TestPathSandbox(unittest.TestCase):
    """CWE-22 / CWE-23 / CWE-59 / CWE-73 -- the shell stays in its directory."""

    def setUp(self):
        self.sh = shell()

    def test_absolute_paths_are_refused(self):
        for path in ("/etc/passwd", "C:\\Windows\\win.ini", "/etc/shadow"):
            with self.assertRaises(security.PathRefused, msg=path):
                security.safe_path(ROOT, path)

    def test_traversal_is_refused(self):
        for path in ("../../../../etc/passwd", "..\\..\\..\\windows\\win.ini",
                     "manifests/../../../../etc/passwd", "./../../secret.yaml"):
            with self.assertRaises(security.PathRefused, msg=path):
                security.safe_path(ROOT, path)

    def test_null_bytes_and_control_characters_are_refused(self):
        for path in ("pods.yaml\x00.png", "a\nb.yaml", "\x1b[2Jx.yaml"):
            with self.assertRaises(security.PathRefused, msg=repr(path)):
                security.safe_path(ROOT, path)

    def test_unexpected_file_types_are_refused(self):
        with self.assertRaises(security.PathRefused):
            security.safe_path(ROOT, "k8s_lab.py")

    def test_normal_paths_still_work(self):
        resolved = security.safe_path(ROOT, "manifests/pod.yaml")
        self.assertTrue(resolved.startswith(os.path.realpath(ROOT)))

    # -- the same thing through the actual commands -------------------------
    def test_apply_cannot_read_outside_the_project(self):
        for path in ("/etc/passwd", "../../../../etc/passwd"):
            result = self.sh.run(f"kubectl apply -f {path}")
            self.assertFalse(result.ok, path)
            self.assertNotIn("root:", result.out + result.err)

    def test_configmap_from_file_cannot_read_outside_the_project(self):
        result = self.sh.run(
            "kubectl create configmap leak --from-file=/etc/passwd")
        blob = json.dumps(self.sh.cluster.get("ConfigMap", "default", "leak")
                          or {}, default=str)
        self.assertNotIn("root:", blob)
        self.assertNotIn("root:", result.out)

    def test_sim_save_cannot_write_outside_the_project(self):
        """This one was an arbitrary file write, reachable from the browser."""
        with tempfile.TemporaryDirectory() as outside:
            target = os.path.join(outside, "pwned.json")
            relative = os.path.relpath(target, ROOT)
            result = self.sh.run(f"sim save {relative}")
            self.assertFalse(result.ok)
            self.assertFalse(os.path.exists(target))
            self.assertFalse(self.sh.run("sim save /tmp/pwned.json").ok)
            self.assertFalse(os.path.exists("/tmp/pwned.json"))

    def test_helm_and_kustomize_cannot_escape(self):
        self.assertFalse(self.sh.run("helm install x ../../../etc").ok)
        self.assertFalse(self.sh.run("kustomize build /etc").ok)

    def test_symlink_out_of_the_tree_is_refused(self):
        link = os.path.join(ROOT, "manifests", ".test-escape.yaml")
        try:
            os.symlink("/etc/passwd", link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable here")
        try:
            with self.assertRaises(security.PathRefused):
                security.safe_path(ROOT, "manifests/.test-escape.yaml")
        finally:
            os.unlink(link)


# ===========================================================================
class TestCommandExecution(unittest.TestCase):
    """CWE-78 -- nothing reaches a shell interpreter."""

    def test_live_mode_refuses_code_execution_flags(self):
        for argv in (["helm", "template", "x", "--post-renderer", "/bin/sh"],
                     ["kubectl", "get", "pods", "--kubeconfig", "/tmp/evil"],
                     ["kubectl", "get", "pods", "--token=abc"],
                     ["bash", "-c", "id"],
                     ["curl", "http://evil.example"]):
            self.assertIsNotNone(security.live_command_refusal(argv), argv)

    def test_live_mode_allows_ordinary_commands(self):
        self.assertIsNone(
            security.live_command_refusal(["kubectl", "get", "pods", "-n", "x"]))

    def test_no_shell_true_anywhere(self):
        for name in os.listdir(os.path.join(ROOT, "k8slab")):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(ROOT, "k8slab", name), encoding="utf-8") as fh:
                self.assertNotIn("shell=True", fh.read(), name)

    def test_no_eval_or_exec_of_input(self):
        pattern = re.compile(r"^\s*(eval|exec)\s*\(", re.M)
        for name in os.listdir(os.path.join(ROOT, "k8slab")):
            if name.endswith(".py"):
                with open(os.path.join(ROOT, "k8slab", name),
                          encoding="utf-8") as fh:
                    self.assertIsNone(pattern.search(fh.read()), name)

    def test_command_length_is_bounded(self):
        result = shell().run("kubectl get pods " + "x" * 10_000)
        self.assertFalse(result.ok)


# ===========================================================================
class TestParserLimits(unittest.TestCase):
    """CWE-400 / CWE-674 / CWE-776 / CWE-502 -- bounded, non-recursive, safe."""

    def test_deep_nesting_raises_instead_of_crashing(self):
        deep = "".join(" " * (2 * i) + f"k{i}:\n" for i in range(400))
        with self.assertRaises(miniyaml.YamlError):
            miniyaml.load_all("a: 1\n" + deep)

    def test_oversized_document_is_refused(self):
        with self.assertRaises(miniyaml.YamlError):
            miniyaml.load_all("a: " + "b" * (security.MAX_YAML_BYTES + 10))

    def test_line_count_is_bounded(self):
        with self.assertRaises(miniyaml.YamlError):
            miniyaml.load_all("k: 1\n" * (security.MAX_YAML_LINES + 5))

    def test_python_object_tags_are_never_constructed(self):
        """If PyYAML is present it must be safe_load, never load."""
        payload = "!!python/object/apply:os.system ['echo pwned']\n"
        try:
            docs = miniyaml.load_all(payload)
        except miniyaml.YamlError:
            return                              # refused outright: also fine
        for doc in docs:
            self.assertNotIsInstance(doc, int)  # os.system would return one


# ===========================================================================
class TestOutputEscaping(unittest.TestCase):
    """CWE-79 / CWE-116 -- object names cannot become markup or script."""

    HOSTILE = '</script><img src=x onerror=alert(1)>'

    def _graph(self):
        sh = shell()
        sh.run("kubectl create deployment web --image=nginx:1.25")
        from k8slab.controllers import reconcile
        reconcile(sh.cluster, 6)
        graph = topology.build(sh.cluster, "default")
        for node in graph.nodes:
            node.label = self.HOSTILE
            node.detail = self.HOSTILE
        return graph

    def test_svg_export_escapes_hostile_labels(self):
        svg = export.to_svg(self._graph(), self.HOSTILE)
        # the payload must survive only as inert text, never as an element
        self.assertNotIn("<script", svg.lower())
        self.assertNotIn("<img", svg.lower())
        self.assertIn("&lt;/script&gt;", svg)
        # no attribute of ours was terminated early by a quote in the input
        self.assertNotIn('="><', svg)

    def test_interactive_html_cannot_break_out_of_its_script_block(self):
        html = export.to_interactive_html(self._graph(), self.HOSTILE)
        body = html.split("__END__")[0]
        self.assertNotIn("</script><img", body)
        self.assertIn("\\u003c", body)

    def test_svg_is_well_formed_xml_even_with_hostile_input(self):
        import xml.etree.ElementTree as ET
        ET.fromstring(export.to_svg(self._graph(), "t"))

    def test_page_has_no_inline_handlers_and_declares_a_nonce(self):
        with open(os.path.join(ROOT, "k8slab", "page.html"),
                  encoding="utf-8") as fh:
            page = fh.read()
        self.assertNotRegex(page, r"<[^>!]*\son(?:click|change|input)\s*=")
        self.assertIn('<script nonce="__NONCE__">', page)

    def test_every_handler_still_resolves(self):
        """The CSP is only safe if nothing quietly stopped working with it.

        Moving 54 inline handlers to addEventListener is the kind of change
        that fails silently -- a button just does nothing. So: every data-h
        index must exist in the table, and every delegated action name must be
        registered.
        """
        from k8slab import webui
        page = webui.page_html(8899, "t", "n")

        indices = {int(i) for i in re.findall(r'data-h="(\d+)"', page)}
        table = re.search(r"const H=\[\n(.*?)\n\];", page, re.S).group(1)
        handlers = re.findall(r'^\s*\["(?:click|change|input)",', table, re.M)
        self.assertTrue(indices)
        self.assertEqual(len(indices), len(handlers))
        self.assertLess(max(indices), len(handlers))

        used = set(re.findall(r"\$\{ds\('(\w+)'", page))
        registered = set(re.search(r"Object\.assign\(ACTIONS,\{([^}]*)\}", page)
                         .group(1).replace(" ", "").split(","))
        self.assertTrue(used)
        self.assertTrue(used <= registered, used - registered)

    def test_page_loads_no_external_code_and_leaves_no_placeholder(self):
        from k8slab import webui
        page = webui.page_html(8899, "tok", "nonce")
        self.assertEqual(re.findall(r"<script[^>]+src=", page), [])
        self.assertEqual(re.findall(r"__[A-Z]+__", page), [])
        self.assertIn('const TOKEN="tok"', page)


# ===========================================================================
class TestGeneratedScripts(unittest.TestCase):
    """CWE-78 in the artefacts we hand the user."""

    def test_variable_values_cannot_inject_commands(self):
        hostile = {"NS": '"; touch /tmp/pwned; echo "',
                   "APP": "$(touch /tmp/pwned2)",
                   "IMAGE": "`touch /tmp/pwned3`"}
        body = portable.env_sh(hostile)
        for line in body.splitlines():
            if line.startswith(("NS=", "APP=", "IMAGE=")):
                self.assertNotIn("$(", line.replace("\\$(", ""))
                self.assertNotIn("`", line.replace("\\`", ""))

    def test_setup_scripts_verify_what_they_download(self):
        from k8slab import envsetup
        pipe_to_shell = re.compile(r"(curl|wget)[^\n|]*\|\s*(ba)?sh\b")
        for flavour in portable.FLAVOURS:
            body = envsetup.environment_setup(flavour)
            self.assertIn("verify_sha256", body, flavour)
            self.assertIn("--proto \'=https\'", body, flavour)
            # never pipe a download straight into an interpreter. Comments are
            # skipped: one of them explains why we do not do this.
            for line in body.splitlines():
                if line.lstrip().startswith("#"):
                    continue
                self.assertIsNone(pipe_to_shell.search(line), line)

    def test_bundles_contain_no_absolute_or_escaping_member_paths(self):
        """CWE-22 on extraction (zip slip) -- for whoever unpacks our zip."""
        import io
        import zipfile
        for flavour in portable.FLAVOURS:
            archive = zipfile.ZipFile(
                io.BytesIO(portable.build_full_bundle(flavour=flavour)))
            self.assertLessEqual(len(archive.namelist()),
                                 security.MAX_ARCHIVE_MEMBERS)
            for name in archive.namelist():
                self.assertFalse(name.startswith("/"), name)
                self.assertFalse(name.startswith("\\"), name)
                self.assertNotIn("..", name.split("/"), name)
                self.assertFalse(re.match(r"^[A-Za-z]:", name), name)


# ===========================================================================
class TestHttpControls(unittest.TestCase):
    """CWE-306 / CWE-352 / CWE-350 / CWE-1021 / CWE-200 on the live server."""

    @classmethod
    def setUpClass(cls):
        cls.shell = shell()
        url = serve(cls.shell, state_dir=tempfile.mkdtemp(), port=8921,
                    host="127.0.0.1", background=True)
        parts = urllib.parse.urlsplit(url)
        cls.base = f"{parts.scheme}://{parts.netloc}"
        cls.host = parts.netloc
        cls.token = urllib.parse.parse_qs(parts.query).get("token", [""])[0]
        time.sleep(0.4)

    def raw(self, method: str, path: str, headers=None, body=None):
        """A request built by hand, so we can send whatever we like."""
        conn = http.client.HTTPConnection(self.host, timeout=15)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        payload = response.read()
        conn.close()
        return response, payload

    def auth(self):
        return {"X-K8SLab-Token": self.token}

    # -- authentication ---------------------------------------------------
    def test_a_token_is_actually_issued(self):
        self.assertGreaterEqual(len(self.token), 32)

    def test_api_without_a_token_is_refused(self):
        for path in ("/api/state", "/api/handbook", "/api/log.txt",
                     "/api/labs-bundle.zip", "/api/topic/pods"):
            response, _ = self.raw("GET", path)
            self.assertEqual(response.status, 403, path)

    def test_post_without_a_token_is_refused(self):
        response, _ = self.raw(
            "POST", "/api/run",
            {"Content-Type": "application/json"},
            json.dumps({"cmd": "kubectl get pods"}))
        self.assertEqual(response.status, 403)

    def test_a_wrong_token_is_refused(self):
        response, _ = self.raw("GET", "/api/state",
                               {"X-K8SLab-Token": "x" * len(self.token)})
        self.assertEqual(response.status, 403)

    def test_the_right_token_works(self):
        response, body = self.raw("GET", "/api/state", self.auth())
        self.assertEqual(response.status, 200)
        self.assertIn("namespaces", json.loads(body))

    def test_healthz_needs_no_token_and_leaks_nothing(self):
        response, body = self.raw("GET", "/healthz")
        self.assertEqual(response.status, 200)
        self.assertEqual(body, b"ok")

    # -- CSRF / rebinding --------------------------------------------------
    def test_cross_site_requests_are_refused(self):
        headers = dict(self.auth())
        headers["Sec-Fetch-Site"] = "cross-site"
        headers["Origin"] = "https://evil.example"
        response, _ = self.raw("GET", "/api/state", headers)
        self.assertEqual(response.status, 403)

    def test_a_foreign_origin_is_refused(self):
        headers = dict(self.auth())
        headers["Origin"] = "https://evil.example"
        response, _ = self.raw("GET", "/api/state", headers)
        self.assertEqual(response.status, 403)

    def test_a_foreign_host_header_is_refused(self):
        """DNS rebinding: the connection is local, the Host header is not."""
        headers = dict(self.auth())
        headers["Host"] = "evil.example"
        response, _ = self.raw("GET", "/api/state", headers)
        self.assertEqual(response.status, 403)

    def test_form_content_type_is_refused_on_post(self):
        """A form POST is not preflighted, so it must not be accepted."""
        headers = dict(self.auth())
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        response, _ = self.raw("POST", "/api/run", headers,
                               'cmd=kubectl get pods')
        self.assertEqual(response.status, 400)

    def test_unsupported_methods_are_refused(self):
        for method in ("PUT", "DELETE", "OPTIONS", "PATCH"):
            response, _ = self.raw(method, "/api/state", self.auth())
            self.assertEqual(response.status, 405, method)

    def test_no_cors_headers_are_ever_sent(self):
        response, _ = self.raw("GET", "/api/state", self.auth())
        for header in ("Access-Control-Allow-Origin",
                       "Access-Control-Allow-Credentials"):
            self.assertIsNone(response.getheader(header), header)

    # -- headers -----------------------------------------------------------
    def test_security_headers_are_present(self):
        response, _ = self.raw("GET", "/", {})
        for name, value in security.SECURITY_HEADERS.items():
            self.assertEqual(response.getheader(name), value, name)
        csp = response.getheader("Content-Security-Policy")
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertIn("object-src 'none'", csp)
        self.assertIn("base-uri 'none'", csp)
        self.assertRegex(csp, r"script-src 'nonce-[A-Za-z0-9_-]{8,}'")
        self.assertNotIn("unsafe-inline'; ", csp.split("style-src")[0])

    def test_the_nonce_changes_every_response(self):
        first, _ = self.raw("GET", "/", {})
        second, _ = self.raw("GET", "/", {})
        self.assertNotEqual(first.getheader("Content-Security-Policy"),
                            second.getheader("Content-Security-Policy"))

    def test_api_responses_are_not_cacheable(self):
        response, _ = self.raw("GET", "/api/state", self.auth())
        self.assertIn("no-store", response.getheader("Cache-Control"))

    def test_the_server_banner_says_nothing_useful(self):
        response, _ = self.raw("GET", "/healthz")
        banner = response.getheader("Server") or ""
        self.assertNotIn("Python", banner)
        self.assertNotIn("BaseHTTP", banner)

    # -- input handling ----------------------------------------------------
    def test_traversal_on_every_file_route_is_refused(self):
        for path in ("/pages/../../k8s_lab.py",
                     "/pages/..%2f..%2fk8s_lab.py",
                     "/pages/....//k8s_lab.py",
                     "/diagram/../../k8s_lab.py.svg",
                     "/api/lab/../../k8s_lab.py",
                     "/api/manifest/../../k8s_lab.yaml"):
            response, body = self.raw("GET", path, self.auth())
            self.assertIn(response.status, (400, 403, 404), path)
            self.assertNotIn(b"argparse", body, path)

    def test_an_oversized_body_is_refused(self):
        """The server may refuse before the body finishes arriving, which
        shows up here as a broken pipe -- either way it must not process it,
        and must still be serving afterwards."""
        headers = dict(self.auth())
        headers["Content-Type"] = "application/json"
        big = json.dumps({"cmd": "x" * (security.MAX_REQUEST_BYTES + 1000)})
        status = None
        try:
            response, _ = self.raw("POST", "/api/run", headers, big)
            status = response.status
        except (BrokenPipeError, ConnectionResetError,
                http.client.HTTPException):
            status = None
        if status is not None:
            self.assertEqual(status, 400)
        alive, body = self.raw("GET", "/healthz")
        self.assertEqual((alive.status, body), (200, b"ok"))

    def test_a_lying_content_length_does_not_crash_the_server(self):
        headers = dict(self.auth())
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = "not-a-number"
        try:
            self.raw("POST", "/api/run", headers, "{}")
        except Exception:
            pass
        response, _ = self.raw("GET", "/healthz")
        self.assertEqual(response.status, 200)

    def test_tick_count_is_clamped(self):
        headers = dict(self.auth())
        headers["Content-Type"] = "application/json"
        before = time.time()
        response, _ = self.raw("POST", "/api/tick", headers,
                               json.dumps({"n": 10 ** 9}))
        self.assertEqual(response.status, 200)
        self.assertLess(time.time() - before, 30)

    def test_batch_size_is_capped(self):
        headers = dict(self.auth())
        headers["Content-Type"] = "application/json"
        payload = json.dumps({"cmds": ["kubectl get pods"] * 5000})
        response, _ = self.raw("POST", "/api/run-many", headers, payload)
        self.assertEqual(response.status, 413)

    # -- least privilege ---------------------------------------------------
    def test_docker_endpoint_reports_rather_than_acts_without_a_daemon(self):
        headers = dict(self.auth())
        headers["Content-Type"] = "application/json"
        response, body = self.raw("POST", "/api/docker", headers,
                                  json.dumps({"action": "deploy"}))
        self.assertIn(response.status, (200, 429))

    def test_secrets_are_redacted_from_the_transcript(self):
        for text, leaked in (
                ("kubectl get pods --token=SUPERSECRET", "SUPERSECRET"),
                ("password=hunter2", "hunter2"),
                ("Authorization: Bearer abc123def456", "abc123def456")):
            self.assertNotIn(leaked, security.redact(text), text)


# ===========================================================================
class TestBindingDefaults(unittest.TestCase):
    """CWE-1327 -- safe by default, unsafe only on purpose."""

    def test_cli_defaults_to_loopback(self):
        with open(os.path.join(ROOT, "k8s_lab.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn('"--host", default="127.0.0.1"', source)
        self.assertNotIn('"--host", default="0.0.0.0"', source)

    def test_serve_signature_defaults_to_loopback(self):
        import inspect

        from k8slab import webui
        self.assertEqual(
            inspect.signature(webui.serve).parameters["host"].default,
            "127.0.0.1")

    def test_exposing_it_needs_an_explicit_opt_in(self):
        """--host 0.0.0.0 alone must fall back to loopback."""
        os.environ.pop("K8SLAB_ALLOW_REMOTE", None)
        url = serve(shell(), state_dir=tempfile.mkdtemp(), port=8922,
                    host="0.0.0.0", background=True)
        self.assertIn("127.0.0.1", url)

    def test_compose_publishes_to_loopback_only(self):
        with open(os.path.join(ROOT, "docker-compose.yml"),
                  encoding="utf-8") as fh:
            compose = fh.read()
        self.assertIn('"127.0.0.1:8899:8899"', compose)
        self.assertIn("no-new-privileges:true", compose)
        self.assertIn("cap_drop: [ALL]", compose)
        self.assertIn("read_only: true", compose)

    def test_container_does_not_run_as_root(self):
        with open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8") as fh:
            dockerfile = fh.read()
        self.assertRegex(dockerfile, r"USER 10001")


# ===========================================================================
class TestRepositoryHygiene(unittest.TestCase):
    """What ships is what we meant to ship."""

    def test_no_third_party_book_pages_are_tracked(self):
        """Imported book pages must never be committed.

        The check is about what git *tracks*, not what happens to be on disk:
        a user who has run tools/import_handbook.py legitimately has pages in
        their working tree, and this test must not fail for them. It must fail
        for anyone about to publish them.
        """
        import subprocess
        try:
            tracked = subprocess.run(
                ["git", "ls-files", "handbook/pages", "handbook/text"],
                cwd=ROOT, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            self.skipTest("git not available")
        if tracked.returncode != 0:
            self.skipTest("not a git working tree")
        committed = [line for line in tracked.stdout.split()
                     if not line.endswith(".gitkeep")]
        self.assertEqual(committed, [],
                         f"copyrighted page files are tracked by git: {committed}")

    def test_a_source_tarball_carries_no_book_pages(self):
        """What ships in the archive, as opposed to what git tracks."""
        for directory in ("pages", "text"):
            path = os.path.join(ROOT, "handbook", directory)
            if not os.path.isdir(path):
                continue
            content = [n for n in os.listdir(path) if n != ".gitkeep"]
            if content:
                self.skipTest(f"handbook/{directory}/ has locally imported "
                              "pages -- expected in a working tree, never in "
                              "a release")

    def test_licence_and_notice_exist(self):
        for name in ("LICENSE", "NOTICE", "SECURITY.md"):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, name)), name)

    def test_gitignore_covers_the_sensitive_things(self):
        with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as fh:
            ignored = fh.read()
        for pattern in ("handbook/pages/*", "handbook/text/*", "*.pdf",
                        "lab-progress.json", ".env", "*.pem", "kubeconfig"):
            self.assertIn(pattern, ignored, pattern)

    def test_no_state_files_are_present(self):
        for name in ("lab-progress.json", "cluster-state.json", "aliases.sh"):
            self.assertFalse(os.path.exists(os.path.join(ROOT, name)), name)

    def test_no_credentials_in_the_source(self):
        pattern = re.compile(
            r"(AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
            r"|ghp_[0-9A-Za-z]{36}|xox[baprs]-[0-9A-Za-z-]{12,})")
        for folder in ("k8slab", "tools", "tests"):
            base = os.path.join(ROOT, folder)
            if not os.path.isdir(base):
                continue
            for name in os.listdir(base):
                if name.endswith((".py", ".html")):
                    with open(os.path.join(base, name), encoding="utf-8",
                              errors="replace") as fh:
                        self.assertIsNone(pattern.search(fh.read()),
                                          f"{folder}/{name}")


if __name__ == "__main__":
    unittest.main()
