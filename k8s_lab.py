#!/usr/bin/env python3
"""k8s-practice-lab -- practise every concept in the Kubernetes Handbook.

ONE COMMAND RUNS EVERYTHING:

    python k8s_lab.py            starts the lab and opens it in your browser

Other ways in, if you want them:

    python k8s_lab.py --gui                desktop window (Tkinter)
    python k8s_lab.py --cli                plain terminal REPL
    python k8s_lab.py --script labs/04-deployments.kubectl        preload a script
    python k8s_lab.py --list-labs          show the guided labs
    python k8s_lab.py --live               drive a REAL cluster with your kubectl
    python k8s_lab.py --docker             build + run the container in one step
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from k8slab import __version__                                        # noqa: E402
from k8slab.kubectl import Shell                                      # noqa: E402
from k8slab import export, handbook, labs as lab_index, topology      # noqa: E402

BANNER = r"""
   _    ___      _                    _   _                  _       _
  | | _( _ ) ___| |    _ __  _ __ __ _| |_(_) ___ ___    ___| | __ _| |__
  | |/ / _ \/ __| |   | '_ \| '__/ _` | __| |/ __/ _ \  / __| |/ _` | '_ \
  |   < (_) \__ \_|   | |_) | | | (_| | |_| | (_|  __/ | (__| | (_| | |_) |
  |_|\_\___/|___(_)   | .__/|_|  \__,_|\__|_|\___\___|  \___|_|\__,_|_.__/
                      |_|      a Kubernetes Handbook simulator
"""


def run_cli(shell: Shell, script: str = "") -> None:
    print(BANNER)
    print(f"  v{__version__}  -  {len(handbook.TOPICS)} handbook topics, "
          f"{len(lab_index.LABS)} labs, {handbook.total_pages()} handbook pages")
    print("  type `help` for commands, `sim` for lab controls, `exit` to quit\n")
    if script:
        _play(shell, script)
    while True:
        try:
            line = input(f"{shell.cluster.current_namespace} $ ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if line in ("exit", "quit", ":q"):
            break
        if line == "topo":
            graph = topology.build(shell.cluster, shell.cluster.current_namespace)
            print(_ascii_topology(graph))
            continue
        result = shell.run(line)
        if result.out == "\x1bCLEAR":
            # ANSI erase-display + cursor-home. No shell, no child process --
            # os.system("clear") would hand a string to /bin/sh for no reason
            # (CWE-78: the argument is a literal today, but the pattern is the
            # bug). Falls back to blank lines where ANSI is unsupported.
            if os.environ.get("TERM") or os.name != "nt":
                print("\033[H\033[2J\033[3J", end="", flush=True)
            else:
                print("\n" * 60)
            continue
        text = result.out if result.ok else result.err
        if text:
            print(text)
        if result.topic and result.topic in handbook.TOPICS:
            topic = handbook.TOPICS[result.topic]
            print(f"\n  \033[36m# handbook p{topic.pages[0]}: {topic.title}\033[0m")


def _play(shell: Shell, script: str) -> None:
    path = script if os.path.isfile(script) else os.path.join("labs", script)
    if not os.path.isfile(path):
        lab = lab_index.by_file(script)
        path = lab.path if lab else path
    if not os.path.isfile(path):
        print(f"script not found: {script}")
        return
    print(f"─── replaying {path} ───\n")
    expect = False
    with open(path, encoding="utf-8") as handle:
        for raw in handle.read().splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#!expect-error"):
                expect = True
                continue
            if line.startswith("#"):
                print(f"\033[90m{line}\033[0m")
                continue
            print(f"\033[36m{shell.cluster.current_namespace} $ {line}\033[0m")
            result = shell.run(line)
            if result.ok:
                text = result.out
            elif expect:
                text = f"\033[33m{result.err}\n(expected: this command is meant to "
                text += "fail -- that rejection is the lesson)\033[0m"
            else:
                text = f"\033[31m{result.err}\033[0m"
            expect = False
            if text:
                print(text)
            print()


def _ascii_topology(graph) -> str:
    """A quick text topology for the plain CLI."""
    lines = ["", "  topology"]
    children = {}
    for edge in graph.edges:
        if edge.kind == "owns":
            children.setdefault(edge.src, []).append(edge.dst)
    parents = {e.dst for e in graph.edges if e.kind == "owns"}
    roots = [n for n in graph.nodes if n.id not in parents and n.tier >= 0]

    def walk(node_id: str, depth: int) -> None:
        node = graph.by_id(node_id)
        if node is None:
            return
        mark = {"ok": "*", "warn": "!", "error": "x", "pending": "."}[node.status]
        lines.append("  " + "   " * depth + f"{mark} {node.kind:<14} {node.name} "
                                            f"{node.sublabel}")
        for child in children.get(node_id, []):
            walk(child, depth + 1)

    for root in roots:
        walk(root.id, 0)
    rail = [n for n in graph.nodes if n.tier < 0]
    if rail:
        lines.append("\n  config & storage")
        for node in rail:
            lines.append(f"    - {node.kind:<24} {node.name}  {node.sublabel}")
    stats = graph.stats
    lines.append(f"\n  {stats['nodes']} nodes | {stats['pods']} pods | "
                 f"{stats['running']} running | {stats['problems']} problems | "
                 f"tick {stats['tick']}\n")
    return "\n".join(lines)


def list_labs() -> None:
    print("\n  guided labs (labs/*.kubectl)\n")
    for lab in lab_index.LABS:
        mark = " " if lab.exists() else "?"
        print(f"  {mark} {lab.file:<32} {lab.title}")
        print(f"      {lab.goal}")
    print("\n  run one with:  python k8s_lab.py --script labs/<file>\n")


def show_coverage() -> None:
    report = handbook.coverage()
    print("\n  What this lab covers, by certification domain\n")
    for exam, domains in report.items():
        print(f"  {exam}")
        for name, info in domains.items():
            print(f"    {info['weight']:>3}%  {name}")
            for title in info["topics"]:
                print(f"           - {title}")
        print()
    print("  Not covered here (do these on a real cluster):")
    for item in handbook.NOT_COVERED:
        print(f"    * {item}")
    print()


def docker_helper(action: str, port: int) -> None:
    root = HERE
    deploy = [["docker", "build", "-t", "k8s-practice-lab", "."],
              ["docker", "rm", "-f", "k8s-lab"],
              ["docker", "run", "-d", "--name", "k8s-lab", "-p", f"{port}:8899",
               "k8s-practice-lab"]]
    commands = {
        "deploy": deploy, "up": deploy, "build": [deploy[0]], "run": deploy[1:],
        "compose": [["docker", "compose", "up", "-d", "--build"]],
        "stop": [["docker", "rm", "-f", "k8s-lab"]],
    }.get(action, None)
    if commands is None:
        print(f"unknown docker action: {action}")
        return
    if shutil.which("docker") is None:
        print("docker is not on your PATH. The commands you need are:\n")
        for argv in commands:
            print("  " + " ".join(argv))
        return
    for argv in commands:
        print("$ " + " ".join(argv))
        subprocess.run(argv, cwd=root)
    if action in ("deploy", "up", "run", "compose"):
        print(f"\n  open the URL printed above (it carries this session's token)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="k8s_lab", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_argument_group("interface")
    mode.add_argument("--gui", action="store_true",
                      help="desktop window (Tkinter) instead of the browser UI")
    mode.add_argument("--web", action="store_true",
                      help="browser UI (this is the default)")
    parser.add_argument("--no-browser", action="store_true",
                        help="start the web UI but do not open a browser tab")
    mode.add_argument("--cli", action="store_true", help="plain terminal REPL")
    parser.add_argument("--script", "-s", metavar="FILE",
                        help="preload a command script and replay it")
    parser.add_argument("--port", type=int, default=8899, help="web UI port")
    parser.add_argument("--host", default="127.0.0.1",
                        help="web UI bind address. Loopback by default: this UI "
                             "runs commands and can build container images, so "
                             "exposing it needs K8SLAB_ALLOW_REMOTE=1 too.")
    parser.add_argument("--dir", default=HERE,
                        help="working directory for manifests/labs/charts")
    parser.add_argument("--live", action="store_true",
                        help="send commands to a REAL cluster via your kubectl")
    parser.add_argument("--export", metavar="FILE",
                        help="write the final topology to an .svg (or .dot) file")
    parser.add_argument("--list-labs", action="store_true")
    parser.add_argument("--certs", action="store_true",
                        help="show CKA/CKAD domain coverage")
    parser.add_argument("--export-commands", metavar="FILE", nargs="?",
                        const="k8s-lab-commands.zip",
                        help="write the labs as runnable shell scripts for a real "
                             "cluster (minikube, kind, EKS...)")
    parser.add_argument("--docker", nargs="?", const="deploy",
                        choices=["deploy", "build", "run", "up", "compose", "stop"],
                        help="one-step container deployment (build + run + open)")
    parser.add_argument("--version", action="version",
                        version=f"k8s-practice-lab {__version__}")
    args = parser.parse_args(argv)

    if args.list_labs:
        list_labs()
        return 0
    if args.certs:
        show_coverage()
        return 0
    if args.export_commands:
        from k8slab import portable
        for flavour in portable.FLAVOURS:
            name = args.export_commands
            if name == "k8s-lab-commands.zip":
                name = f"k8s-labs-{flavour}.zip"
            elif len(portable.FLAVOURS) > 1 and flavour != "kubernetes":
                name = name.replace(".zip", f"-{flavour}.zip")
            with open(name, "wb") as handle:
                handle.write(portable.build_full_bundle(args.dir, flavour))
            print(f"  wrote {name}")
        print("\n  unzip either one, then:\n"
              '    cd "Lab .sh scripts"\n'
              "    ./00-setup-minikube.sh   # minikube zip only\n"
              "    ./run-all.sh\n")
        return 0
    if args.docker:
        docker_helper(args.docker, args.port)
        return 0

    shell = Shell(base_dir=args.dir, live=args.live)
    if args.live:
        print("[live mode] kubectl/helm commands are forwarded to your real cluster.")

    if args.cli:
        run_cli(shell, args.script or "")
    elif args.gui:
        try:
            from k8slab.gui import launch
        except SystemExit as exc:
            print(exc)
            print("\nFalling back to the browser UI...\n")
            from k8slab.webui import serve
            serve(shell, port=args.port, host=args.host,
                  open_browser=not args.no_browser)
            return 0
        launch(base_dir=args.dir, live=args.live, web_port=args.port,
               script=args.script)
    else:
        from k8slab.webui import serve
        if args.script:
            _play(shell, args.script)
        print(BANNER)
        serve(shell, port=args.port, host=args.host,
              open_browser=not args.no_browser)

    if args.export:
        graph = topology.build(shell.cluster, None, "logical", include_system=True)
        if args.export.endswith(".dot"):
            with open(args.export, "w", encoding="utf-8") as handle:
                handle.write(export.to_dot(graph))
        else:
            export.write_svg(graph, args.export, "k8s-practice-lab topology")
        print(f"topology written to {args.export}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
