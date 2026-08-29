# k8s-practice-lab

A Kubernetes practice environment that simulates a 4-node cluster in pure
Python, so you can type real `kubectl` commands, watch a topology diagram build
itself as you go, and read a concept diagram beside your work — no Docker
daemon, no minikube, no cloud account required.

All 50 topics ship with an **original vector diagram** drawn for this project.
If you own a study guide you would rather read alongside the exercises,
`tools/import_handbook.py` extracts its pages into `handbook/` on your own
machine (see `handbook/README.md`); nothing of the sort is bundled here.

There are 29 guided labs that you can preload and replay one command at a time —
including cluster administration: `kubeadm` upgrades, `etcdctl` backup and
restore, certificates and kubeconfig, enforced NetworkPolicies, and image
registries with pull secrets.

---

## 1. One command

```bash
python k8s_lab.py
```

That starts the lab and opens it in your browser. Python 3.9+ and nothing else —
no pip install, no Docker required. The YAML parser, the web server and the
graphics are all standard library.

It listens on `127.0.0.1` only, and prints a URL containing a one-off access
token — open that exact link. Both are deliberate: this UI runs commands and can
build container images, so it is not something to leave open on a network.

**In a container, also one command:**

```bash
docker compose up --build          # then: docker logs k8s-lab, open the URL
```

or, if you prefer a script that builds, replaces and starts the container in one
step: `./deploy-docker.sh` (Linux/macOS) or `.\deploy-docker.ps1` (Windows).
From the command line, `python k8s_lab.py --docker` does the same thing.

**Or press one button.** The strip at the top of the browser UI has a single
blue **Deploy** button that builds the image, replaces any old container, starts
it and opens it. Everything else (build only, compose, status, logs, stop) is
tucked behind *more ▾*. The desktop GUI has the same button.

> **"failed to connect to the docker API … dockerDesktopLinuxEngine"** means
> Docker is installed but the engine isn't running. Start Docker Desktop (wait
> for the whale to stop animating), or `sudo systemctl start docker` on Linux.
> The Deploy button now checks for this first and tells you in plain English
> instead of dumping the raw error. Nothing else in the lab depends on Docker.

### Other ways in

| Command | What it does |
|---------|--------------|
| `python k8s_lab.py` | browser UI + opens your browser (**the default**) |
| `python k8s_lab.py --gui` | desktop window (Tkinter) instead |
| `python k8s_lab.py --cli` | plain terminal REPL |
| `python k8s_lab.py --script labs/04-deployments.kubectl` | preload a script and replay it |
| `python k8s_lab.py --list-labs` | list the 29 guided labs |
| `python k8s_lab.py --certs` | CKA/CKAD domain coverage |
| `python k8s_lab.py --export-commands` | the same bundle as the **Real cluster** tab, from the terminal |
| `python k8s_lab.py --port 9000 --no-browser` | change the port / don't auto-open |
| `python k8s_lab.py --export topology.svg` | write the topology to SVG (or `.dot`) |
| `python k8s_lab.py --live` | forward commands to a **real** cluster via your `kubectl` |
| `python k8s_lab.py --docker` | build + run + open the container |

> The desktop GUI needs Tkinter (ships with python.org and Windows/macOS
> installers; `sudo apt install python3-tk` on Debian/Ubuntu). If it is missing,
> the app says so and falls back to the browser UI.

---

## 2. What you get

### Preload commands, watch the topology draw itself

Type into the terminal, paste a whole script into the editor and press
**Run as commands**, or pick one of the 29 labs and press **Run all** — every
object you create appears on the topology as it is admitted, scheduled and
started, with a coloured status dot and live status text.

```
kubectl create deployment web --image=nginx:1.25 --replicas=3
kubectl expose deploy/web --port=80
kubectl autoscale deploy/web --cpu-percent=50 --min=2 --max=10
sim load deploy/web 95          # drive the HPA
```

Two views:

* **Logical** — one labelled lane per namespace, each containing
  `Ingress → Service → Deployment → ReplicaSet → Pod` plus a side rail for that
  namespace's ConfigMaps, Secrets, PVCs, PVs, HPAs and NetworkPolicies. Anything
  you create is drawn in the lane of the namespace it was created in, with
  dashed edges showing which pods a Service selects and which config a pod
  mounts. (A PersistentVolume is cluster-scoped, so it is drawn in the lane of
  the claim that bound it.)
* **Physical** — node boxes with their pods packed inside, CPU/memory bars per
  node, taints listed, and an "Unscheduled" box for anything Pending.

Wide tiers wrap onto several rows, so a 30-pod cluster still fits on screen, and
the view auto-fits when it first renders. Click any card for `kubectl describe`
plus the full YAML, or **pop out ⤢** for the topology on its own full-window
canvas.

**Colour tells you which part of Kubernetes you are looking at.** Every handbook
section owns a hue family and every kind gets its own tone inside it — security
red, packaging orange, operations leaf, storage green, cluster administration
deep teal, networking cyan, fundamentals sky, workloads blue, scheduling indigo,
configuration purple, autoscaling magenta. The hues are spread right around the
wheel, and where two sit close the lightness pulls them apart. Edges are tinted
by what they point at. Nothing is yellow (it reads badly on the dark canvas next to
the amber "warning" status dot), and the status dot in each card's corner stays
a separate axis: green ready, amber degraded, red broken. There is a legend
under the canvas.

The **Export** menu gives you: the topology as `.svg`, the topology as a
**self-contained interactive `.html`** (zoom, pan, **drag any object**, full
screen, *Restore layout*, and Save .svg from inside it), the session log as
`.txt` or `.json`, the object dictionary as `.txt`, every exercise manifest as a
`.zip`, and — see below — **the whole lab as runnable shell scripts**.

Your zoom is yours: a background refresh never re-fits the canvas. Only *fit*,
changing the view, or the first paint do that.

### The handbook, next to your work

The sidebar is the handbook's table of contents. Pick a topic — or run a command
about it — and **every panel follows at once**, whichever one you happen to be
looking at:

* **Notes** — summary, key points, common mistakes, interview questions, and
  every command with a one-line note saying exactly what it does. Click a
  command to run it.
* **Page** — two images. First an **original vector diagram** drawn by the app
  for that concept (SVG, so it stays perfectly sharp at any zoom), then the
  **handbook page** at the PDF's own 1024×1536 resolution. Click either for a
  full-screen viewer with zoom (+ / − / fit / 100%, Ctrl+scroll, Esc to close).
* **Script / YAML** — a **Script** / **Manifest** toggle. *Script* holds that
  topic's exercise commands (*Run as commands*); *Manifest* holds a labelled
  starter YAML (*Apply as YAML*). The buttons follow the toggle, so applying a
  command script as YAML is no longer possible. *Download .yaml* saves the
  current one as
  `workloads_deployments_lab04_p6.yaml` — section, topic, lab number and page in
  the filename — and *Download all* gives you a zip of every manifest plus every
  lab script and an `INDEX.txt`.
* **Objects** — the object dictionary (see below).
* **Inspect / Events** — whatever you last clicked on the topology, and the live
  event stream.

Six tabs, one visible at a time.

### The object dictionary

A chronological list of everything that exists, and for each object three
things: **what the kind is for** (the handbook's definition), **what this
particular one does** (read from its own spec — images, replicas, ports,
selectors, capacity, endpoints), and **how it got here**. That last part is the
interesting one:

```
 14. Deployment/web            $ kubectl create deployment web --image=nginx:1.25 --replicas=2
 15. ReplicaSet/web-dxltm5wmr  ↳ created automatically by its owner Deployment/web
 16. Pod/web-dxltm5wmr-njl2w   ↳ created automatically by its owner ReplicaSet/web-…
 19. Service/web               $ kubectl expose deploy/web --port=80
```

One command, three controllers, four objects. Download it as text from the
Export menu.

### Run every lab unattended

**Run all labs** in the header replays all 29 labs back to back with a progress
bar and a live command feed. It runs server-side, so you can leave it going;
press the button again to stop. Nothing is reset between labs, so when it
finishes the topology holds everything the whole curriculum built (~190 objects
across every namespace), and the view switches to *all namespaces* to show it.
Each lab also **ticks off the topics it covers** in the sidebar as it completes,
so the progress bar fills in as it runs.

### Everything in one download — two flavours

Two buttons at the top of the header, each giving one zip:

* **⬇ Labs (kubectl)** — for any cluster you can already reach (kind, k3s, EKS,
  GKE, AKS, or a minikube you started yourself)
* **⬇ Labs (minikube)** — same labs, plus `00-setup-minikube.sh`

```
k8s-labs-<flavour>/
  START-HERE.md
  Lab .sh scripts/
    00-Environment Set up.sh        installs kubectl/helm/kustomize(/minikube)
    01-Minikube cluster set up.sh   minikube flavour only
    run-all.sh, labs/*.sh, env.sh, lib.sh, manifests/ charts/ kustomize/
  Lab .yaml scripts/     one labelled manifest per topic
  Lab .kubectl scripts/  the originals, for this app's terminal
```

**Both flavours install their own tools.** `"00-Environment Set up.sh"` detects
your OS, arch and package manager, reports what is already present, and installs
what is missing — kubectl, helm, kustomize (and minikube, in that flavour) — from
the official sources (`dl.k8s.io`, `get-helm-3`, the kustomize and minikube
release scripts, or brew/apt where available). It asks first (`--yes` skips the
prompt, `--check` only reports), never touches anything already installed, falls
back to `~/.local/bin` when it cannot write to `/usr/local/bin`, and finishes by
writing **`aliases.sh`**.

```bash
cd "Lab .sh scripts"
./"00-Environment Set up.sh"          # installs the tools, writes aliases.sh
source aliases.sh                     # k, kg, kgp, kd, kaf, kdel, kl, kx, kns, h, kz
./"01-Minikube cluster set up.sh"     # minikube flavour: starts the cluster
./run-all.sh
```

On the minikube flavour the aliases point at the profile, and **`kubectl` itself
is aliased** — if no standalone kubectl is installed it becomes
`minikube -p $PROFILE kubectl --`, so every command in the labs and in your own
shell works either way. You also get `mk`, `mkip`, `mkdash`, `mkaddons` and
`mkdocker` (`eval $(minikube -p $PROFILE docker-env)`, which makes lab 29 work
without a registry).

`"01-Minikube cluster set up.sh"` then starts a 3-node profile (`$PROFILE`,
default `k8s-lab`) and enables the addons the labs actually need — ingress for
lab 17, metrics-server for `kubectl top` and the HPA labs, the storage
provisioner for labs 08–09.

`KUBECTL` defaults to `minikube kubectl --` in that flavour, so the scripts use
the version that ships with your cluster. `mkdocker` (i.e.
`eval $(minikube -p $PROFILE docker-env)`) builds images straight into the
cluster, which makes lab 29 work without pushing anywhere. On this flavour
`sim node ... add/stop` becomes a real `minikube node add/stop`, guarded by
`ALLOW_DESTRUCTIVE`.

```
k8s-lab-commands/
  README.md          how to run it, and what every variable means
  env.sh             every value, one per line, each with a comment
  lib.sh             helpers, guards and the k/kgp/kaf aliases
  run-all.sh         ./run-all.sh          or  ./run-all.sh 4 5 6
  labs/04-deployments.sh   … one per lab, runnable on its own
  manifests/ charts/ kustomize/
```

Every value — namespace, app name, images, registry, storage class, ingress
class, timeouts — is a line in `commands/env.sh` with a comment explaining what
it is for. Edit that file, or override per run:
`NS=my-lab IMAGE=nginx:1.27 ./run-all.sh`.

Three kinds of command cannot work from a kubeconfig alone, so they are guarded
and off by default: `kubeadm`/`etcdctl` (`ALLOW_NODE_COMMANDS`), `docker
build/push` (`ALLOW_DOCKER`), and `drain`/`cordon`/`delete namespace`
(`ALLOW_DESTRUCTIVE`). Simulator-only steps (`sim load`, `sim chaos`, …) become
comments that tell you how to get the same effect for real — e.g. a real load
generator instead of a synthetic HPA number. Steps that are *meant* to fail run
through `expect_fail`, which reports "rejected, as intended" and carries on.

`python k8s_lab.py --export-commands` writes both zips from the terminal.

### Deliberate failures are labelled

Some lab steps are *meant* to fail — applying a Deployment whose selector does
not match its template labels, for example. Those lines are marked
`#!expect-error` in the script, and the output comes back in amber with
"expected: this command is meant to fail — that rejection is the lesson", so a
teaching moment never looks like a broken lab.

### Track what you have covered

Every topic in the sidebar shows its **lab number and handbook page**
(`Deployments L04 p6`) and carries a checkbox, with a progress bar above the
list. "Run all labs" ticks them off as it goes, so the count is not zero after a
run — **clear** next to the progress bar unticks everything. Ticks are saved to `lab-progress.json`, so they survive a restart (and a
container rebuild if you mount that file). The **Reset…** menu in the header
offers three scopes: *cluster only*, *progress only*, or *the whole lab*.

### Resize anything

Drag the divider between the sidebar, the topology and the right rail, and the
horizontal handle above the terminal to make the command area taller or shorter.
Double-click that handle to collapse or restore the terminal.

### It behaves like a real cluster

The simulator implements the request path and the control loops the handbook
draws, so the mistakes you make here are the mistakes you make in production:

* **Admission & validation** — a Deployment whose `selector` doesn't match its
  template labels is rejected, `nodePort` must be in 30000–32767, a Pod in a
  namespace that doesn't exist fails, ResourceQuota blocks the pod over the limit.
* **Scheduler** — filtering (taints/tolerations, `nodeSelector`, node affinity,
  pod affinity/anti-affinity, resource fit, cordoned/NotReady nodes) then scoring
  (least-allocated + preferred affinity weights). Unschedulable pods get the real
  `0/4 nodes are available: ...` message.
* **Controllers** — Deployment → ReplicaSet → Pod with rolling updates, revision
  history and `rollout undo`; StatefulSets with ordered startup and per-replica
  PVCs; DaemonSets that follow new nodes; Jobs and CronJobs; endpoint
  reconciliation; PVC binding and dynamic provisioning; HPA and VPA.
* **Failure modes you can trigger on purpose** — `ImagePullBackOff`,
  `CrashLoopBackOff`, `OOMKilled`, `CreateContainerConfigError`, failing
  readiness probes, unschedulable pods, services with no endpoints, node loss.
* **RBAC** — turn enforcement on, become another user, and watch
  `kubectl auth can-i` change as you add Roles and RoleBindings.

### Commands supported

```
kubectl   get describe explain api-resources cluster-info version events
          apply -f/-k  create  delete  run  expose  scale  autoscale  set image
          rollout status|history|undo|restart      patch -p
          label  annotate  taint  cordon  uncordon  drain
          logs  exec  top pods|nodes  auth can-i  wait  diff
          certificate approve|deny
          config view|get-contexts|set-cluster|set-credentials|set-context|use-context
kubeadm   version  token create  join  upgrade plan|apply|node  certs check-expiration|renew
etcdctl   member list  snapshot save|status|restore     (ETCDCTL_API=3 + TLS flags)
docker    build  tag  push  images  rmi                 (registry model)
helm      install  upgrade  uninstall  list  template  repo add
kustomize build DIR                       (or kubectl apply -k DIR)
```

Plus a lab-only `sim` verb, which is how you create the interesting situations:

```
sim tick [n]                    advance the control loop
sim load deploy/web 95          synthetic CPU load -> drives the HPA
sim chaos web crash|oom|imagepull|notready|clear
sim node lab-worker-1 down|up|add|remove
sim connectivity [port]         allow/deny matrix between every pod, with reasons
sim age-certs 340               fast-forward certificate expiry
sim rbac on|off        sim user bob        sim reset
sim save state.json    sim load-state state.json
```

---

## 3. Certification coverage

Both certifications are hands-on and timed, so the value here is repetition:
every domain below maps to topics you can practise by typing.

| CKA domain | Weight | Covered by |
|---|---|---|
| Cluster architecture, installation & configuration | 25% | architecture, **kubeadm join & upgrade**, **etcd backup & restore**, **certificates & CSRs**, **kubeconfig & contexts**, namespaces & quotas, RBAC, ServiceAccounts, authn/authz, admission, CRDs, Helm, Kustomize, cluster autoscaler |
| Workloads & scheduling | 15% | pods, deployments, ReplicaSets, StatefulSets, DaemonSets, Jobs/CronJobs, init & multi-container, labels, taints, affinity, resources, probes, HPA, ConfigMaps |
| Services & networking | 20% | Services, Ingress, Ingress controllers, NetworkPolicies (**enforced**) |
| Storage | 10% | PV, PVC, StorageClasses, dynamic provisioning |
| Troubleshooting | 30% | troubleshooting drills, events, logs, metrics, best practices |

| CKAD domain | Weight | Covered by |
|---|---|---|
| Application design and build | 20% | pods, multi-container patterns, init containers, Jobs/CronJobs, StatefulSets, DaemonSets, volumes, **images & pull secrets** |
| Application deployment | 20% | Deployments, rolling updates & rollback, HPA, Helm, Kustomize, labels/annotations |
| Observability & maintenance | 15% | probes, logs, events, `kubectl top`, debugging drills |
| Environment, configuration & security | 25% | ConfigMaps & Secrets, namespaces & quotas, resources, RBAC, ServiceAccounts, security context |
| Services & networking | 20% | Services, Ingress, Ingress controllers, NetworkPolicies |

`python k8s_lab.py --certs` prints the same table with the topic list under each
domain, and every topic in the UI carries a CKA/CKAD badge.

### Cluster administration (labs 25–29)

These used to be the gaps. They are now practised here, with the real rules
enforced:

| Lab | What you practise | What the lab refuses to let you do |
|---|---|---|
| **25 kubeadm** | `upgrade plan` → `upgrade apply` → per-node drain/upgrade/uncordon, `token create`, `join` | skipping a minor version; upgrading a kubelet before the control plane; upgrading a node you have not drained |
| **26 etcd** | `snapshot save`, `status`, `restore`, then watch deleted objects come back | any etcdctl call missing `--endpoints`/`--cacert`/`--cert`/`--key`, or `ETCDCTL_API=2` |
| **27 certs** | CSR → approve → extract → `set-credentials`/`set-context`/`use-context`; expiry and `certs renew` | using an approved certificate with no RoleBinding (still Forbidden) |
| **28 NetworkPolicy** | connections are **actually evaluated** — `curl` succeeds, then times out, and tells you which policy decided; `sim connectivity` prints the whole allow/deny matrix | reaching a pod that a policy isolates, in either direction |
| **29 images** | `build` → `tag` → `push`, public vs private repos, `create secret docker-registry`, `imagePullSecrets` | pulling an image you only built locally; pulling a private image with no secret |

**What still needs a real cluster** (kind or minikube is enough):

* `kubeadm init` / `reset` — bootstrapping a control plane on real machines. The
  upgrade and join workflow is here; building one from bare hosts is not.
* real TLS material — the CSR→kubeconfig workflow is here, but no keys are
  generated, so `openssl` practice belongs on a real cluster
* real container builds — the registry, pushes, private repos and pull secrets
  are modelled; writing an efficient Dockerfile is a Docker skill
* a real CNI dataplane — policies are enforced here, but no packets move, so
  Calico-vs-Cilium specifics are out of scope
* node-level debugging — systemd, kubelet logs on disk, `crictl`

---

## 4. The 29 labs

| Lab | Topic | Handbook |
|-----|-------|----------|
| 01 | Fundamentals & first look | p3 |
| 02 | Architecture & nodes | p4, 39–44, 49 |
| 03 | Pods | p5 |
| 04 | Deployments, ReplicaSets & rollouts | p6, 8 |
| 05 | Services & discovery | p7 |
| 06 | ConfigMaps & Secrets | p9 |
| 07 | Namespaces & quotas | p10 |
| 08 | PV, PVC, StorageClass, dynamic provisioning | p11–14 |
| 09 | StatefulSets | p15 |
| 10 | DaemonSets | p16 |
| 11 | Jobs & CronJobs | p17–18 |
| 12 | Init containers & sidecars | p19–20 |
| 13 | Labels, selectors & annotations | p21–22 |
| 14 | Taints, tolerations & affinity | p23–25 |
| 15 | Requests, limits & probes | p26–27 |
| 16 | HPA, VPA & cluster autoscaling | p28–30 |
| 17 | Ingress & Ingress Controller | p31–32 |
| 18 | RBAC, ServiceAccounts & NetworkPolicy | p33–38 |
| 19 | Helm & Kustomize | p45–46 |
| 20 | CRDs & operators | p47–48 |
| 21 | Metrics, logs & events | p50–51 |
| 22 | Troubleshooting drills | p52 |
| 23 | Production best practices | p53 |
| 24 | CKA/CKAD speed drills | p54 |
| 25 | kubeadm: join & upgrade | p4 |
| 26 | etcd backup & restore | p39 |
| 27 | Certificates, CSRs & kubeconfig | p36 |
| 28 | NetworkPolicy, actually enforced | p33 |
| 29 | Images, registries & pull secrets | p5 |

Run one straight from the terminal:

```bash
python k8s_lab.py --cli --script labs/22-troubleshooting.kubectl
```

Lab 22 deliberately breaks five workloads and asks you to diagnose each from
`kubectl describe` and `kubectl logs` before it shows you the answer.

---

## 5. Project structure

```
k8s-practice-lab/
├── k8s_lab.py                  entry point (--gui / --web / --cli / --docker)
├── Dockerfile                  container image (runs the web UI on :8899)
├── docker-compose.yml
├── deploy-docker.sh / .ps1     one-command deployment
├── README.md
├── k8slab/                     the application package
│   ├── model.py                resource registry, cluster state, quantities
│   ├── miniyaml.py             dependency-free YAML parser/dumper
│   ├── apiserver.py            validation, defaulting, admission, RBAC
│   ├── scheduler.py            filtering + scoring (handbook p40)
│   ├── controllers.py          every control loop (handbook p41)
│   ├── cluster_factory.py      builds the 4-node lab cluster
│   ├── kubectl.py              kubectl / helm / kustomize / sim command engine
│   ├── printers.py             kubectl-style tables and `describe`
│   ├── topology.py             cluster state -> positioned graph
│   ├── export.py               graph -> SVG / Graphviz
│   ├── handbook.py             50 topics: notes, YAML, gotchas, command
│   │                           explanations, page + certification mapping
│   ├── diagrams.py             original vector concept diagram per topic
│   ├── admin.py                kubeadm, etcdctl, PKI/kubeconfig, registry
│   ├── netpolicy.py            NetworkPolicy evaluation (the lab's data plane)
│   ├── dictionary.py           object dictionary: what exists, why, and who made it
│   ├── portable.py             lab scripts -> runnable bash for a real cluster
│   ├── envsetup.py             the "00-Environment Set up.sh" installer
│   ├── labs.py                 lab index
│   ├── gui.py                  Tkinter desktop UI
│   ├── webui.py                stdlib browser UI + JSON API + Docker deploy
│   └── page.html               the browser UI itself (edit it, reload, done)
├── handbook/                   empty; fill it with tools/import_handbook.py
│   ├── pages/                  your own study-guide scans (git-ignored)
│   ├── text/                   their text, for search (git-ignored)
│   └── README.md               why it is empty + the page -> topic map
├── tools/import_handbook.py    extract a PDF you own into handbook/
├── labs/                       29 preloadable command scripts
├── manifests/                  39 ready-to-apply YAML files
├── charts/webapp/              Helm chart for the Helm lab
├── kustomize/                  base + dev/prod overlays
├── tests/test_lab.py           simulator tests
├── tests/test_ui_features.py   colours, manifests, dictionary, exports
└── tests/test_security.py      path sandbox, auth, CSRF, escaping, limits
                                (249 tests total, no pytest needed)
```

Run the tests with:

```bash
python -m unittest discover -s tests -t . -v
```

---

## 6. Driving a real cluster

```bash
python k8s_lab.py --live
```

In live mode `kubectl`, `helm` and `kustomize` commands are forwarded to the
binaries on your PATH and run against your real current context, while the
topology, handbook and labs work exactly the same. Start with a throwaway
cluster (kind, minikube, k3d) — the labs create and delete objects freely.

---

## 7. Credits and attribution

Everything in this repository is original work, MIT licensed: the simulator, the
29 labs, the notes and command explanations, the vector concept diagrams in
`k8slab/diagrams.py`, and both user interfaces.

The topic ordering follows a published Kubernetes study guide I worked through,
and `handbook/README.md` records which page covers which topic — but **none of
that book is distributed here**. `handbook/pages/` and `handbook/text/` ship
empty and are git-ignored. If you own a copy and want its pages shown beside the
exercises, `tools/import_handbook.py` extracts them locally, for you alone.

See `NOTICE` for trademark attributions (Kubernetes, Docker, CKA/CKAD and the
rest belong to their owners; this project is not affiliated with any of them)
and `SECURITY.md` for how to run this safely and how to report a problem.

Nothing here talks to a real cluster unless you pass `--live`.
