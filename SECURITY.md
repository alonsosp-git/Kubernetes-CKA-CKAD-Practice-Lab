# Security policy

## Reporting a vulnerability

Please use GitHub's **private vulnerability reporting** (Security → Report a
vulnerability) rather than a public issue. I'll acknowledge within a few days
and credit you in the fix unless you'd rather I didn't.

## What this program is

A local teaching tool. It simulates a Kubernetes cluster in memory, serves a
browser UI from the standard library, and can build a Docker image on request.
Nothing is sent anywhere: no telemetry, no analytics, no network calls except
the ones you explicitly ask for (Docker deploy, `--live` mode, and the setup
script's downloads).

## Trust boundaries

| Boundary | What crosses it | Control |
|---|---|---|
| Browser → HTTP server | commands, manifests, exports | session token, Host/Origin checks, JSON-only bodies, size limits |
| HTTP server → shell | `kubectl`/`helm`/`sim` lines | path sandbox, command length cap, no shell interpreter |
| Shell → filesystem | `-f`, `--from-file`, `sim save` | `security.safe_path`: no absolute paths, no traversal, no symlink escape, extension allowlist |
| Shell → OS (`--live` only) | real `kubectl`/`helm` | program allowlist, dangerous-flag denylist, argv list (never a shell string) |
| App → Docker daemon | build/run/stop | loopback-only, rate limited, fixed argv, no user input in the command |
| Your PDF → `handbook/` | page images | local only, git-ignored, never uploaded |

## How to run it safely

* **Leave it on loopback.** The default is `127.0.0.1`. A port that runs
  commands and builds container images has no business on a shared network.
  Exposing it needs both `--host` *and* `K8SLAB_ALLOW_REMOTE=1`, and even then
  the Docker endpoint stays loopback-only.
* **The URL contains the session token.** It's regenerated on every start.
  Don't paste it into a chat.
* **`--live` runs real commands against your real cluster.** It's off by
  default. Point it at a throwaway cluster (minikube, kind), never production.
* **The exported scripts are real.** `run-all.sh` creates and deletes objects.
  The genuinely destructive steps are behind `ALLOW_NODE_COMMANDS`,
  `ALLOW_DOCKER` and `ALLOW_DESTRUCTIVE`, all `no` by default.

## Known, accepted residual risks

* **`style-src 'unsafe-inline'`.** The page uses inline `style` attributes for
  the topology colours. Style injection is a much lower-severity class than
  script injection, and `script-src` carries a nonce with no `unsafe-inline`.
* **Anyone with a local shell account can read the token** from the process
  environment or the terminal. That is inherent to a single-user desktop tool;
  the token defends against other *origins in your browser*, not against a
  hostile local user, who already has your files.
* **`--live` mode trusts your kubeconfig.** It has to; that is the point.
* **The Docker deploy button needs Docker socket access**, which is root
  equivalent on the host. That is Docker's model, not something this app can
  fix — it is why the endpoint is loopback-only, rate-limited, and driven by a
  fixed argv list.

## Supply chain

* No required runtime dependencies. One optional one (Pillow), pinned to a
  version above every known advisory, and checked by `pip-audit` in CI.
* CI runs Bandit, Semgrep, CodeQL, ruff, pip-audit, Trivy (image + IaC),
  ShellCheck on every generated script, and a secret scan over the full
  history. An SBOM is published as a build artifact.
* The environment-setup script downloads over HTTPS only, verifies published
  SHA-256 checksums, and never pipes a download straight into a shell.
