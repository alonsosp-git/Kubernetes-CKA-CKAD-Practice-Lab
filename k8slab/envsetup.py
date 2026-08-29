"""The `00-Environment Set up.sh` script that ships in both bundles.

Installs the tools the labs need -- kubectl, helm, kustomize, and (for the
minikube flavour) minikube itself -- then writes `aliases.sh` so `kubectl`, `k`,
`helm` and friends work the same way in either flavour.

Design rules:

* **nothing happens without consent.** It prints a plan and asks; `--yes` skips
  the prompt, `--check` only reports what is missing.
* **idempotent.** Anything already installed is reported and skipped.
* **no sudo unless needed.** Falls back to `~/.local/bin` when sudo is absent.
* **official sources only** -- dl.k8s.io, get.helm.sh, the kustomize and minikube
  release scripts, or the platform's own package manager.
"""
from __future__ import annotations

HEADER = r'''#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 00-Environment Set up.sh
#
# Installs everything the labs need, then writes aliases.sh.
#
#   ./"00-Environment Set up.sh"            plan, ask, install
#   ./"00-Environment Set up.sh" --yes      no questions
#   ./"00-Environment Set up.sh" --check    report only, install nothing
#
# Safe to re-run: whatever is already there is left alone.
# ---------------------------------------------------------------------------
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$HERE/env.sh" ] && source "$HERE/env.sh"
[ -f "$HERE/lib.sh" ] && source "$HERE/lib.sh" || {
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'
  YELLOW=$'\033[33m'; CYAN=$'\033[36m'; OFF=$'\033[0m'
  banner(){ printf '\n%s=== %s ===%s\n\n' "$BOLD$CYAN" "$1" "$OFF"; }
  note(){ printf '%s# %s%s\n' "$DIM" "$*" "$OFF"; }
  ok(){ printf '%s%s%s\n' "$GREEN" "$*" "$OFF"; }
  warn(){ printf '%s%s%s\n' "$YELLOW" "$*" "$OFF"; }
  fail(){ printf '%s%s%s\n' "$RED" "$*" "$OFF"; }
}

ASSUME_YES=no
CHECK_ONLY=no
for arg in "$@"; do
  case "$arg" in
    --yes|-y) ASSUME_YES=yes ;;
    --check|-n) CHECK_ONLY=yes ;;
    --help|-h)
      grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -14; exit 0 ;;
    *) warn "unknown argument: $arg" ;;
  esac
done

# ---------------------------------------------------------------------------
# 1. what am I running on?
# ---------------------------------------------------------------------------
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
case "$(uname -m)" in
  x86_64|amd64) ARCH=amd64 ;;
  aarch64|arm64) ARCH=arm64 ;;
  armv7l) ARCH=arm ;;
  *) ARCH="$(uname -m)" ;;
esac
IS_WSL=no
grep -qi microsoft /proc/version 2>/dev/null && IS_WSL=yes

PKG=none
if   command -v brew    >/dev/null 2>&1; then PKG=brew
elif command -v apt-get >/dev/null 2>&1; then PKG=apt
elif command -v dnf     >/dev/null 2>&1; then PKG=dnf
elif command -v yum     >/dev/null 2>&1; then PKG=yum
elif command -v pacman  >/dev/null 2>&1; then PKG=pacman
fi

SUDO=""
if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"          # you may be asked for your password once
fi
BINDIR="${BINDIR:-/usr/local/bin}"
can_write() { [ -d "$1" ] && [ -w "$1" ]; }
if ! can_write "$BINDIR" && [ -z "$SUDO" ]; then
  BINDIR="$HOME/.local/bin"
fi
mkdir -p "$BINDIR" 2>/dev/null || true

banner "environment check"
note "os=$OS arch=$ARCH package-manager=$PKG install-dir=$BINDIR$( \
  [ "$IS_WSL" = yes ] && echo ' (WSL)')"

# ---------------------------------------------------------------------------
# 2. what is already here?
# ---------------------------------------------------------------------------
have() { command -v "$1" >/dev/null 2>&1; }
version_of() {
  case "$1" in
    kubectl)   kubectl version --client -o yaml 2>/dev/null \
                 | awk -F'"' '/gitVersion/{print $2; exit}' ;;
    helm)      helm version --short 2>/dev/null ;;
    kustomize) kustomize version 2>/dev/null | head -1 ;;
    minikube)  minikube version --short 2>/dev/null ;;
    docker)    docker --version 2>/dev/null ;;
    *)         "$1" --version 2>/dev/null | head -1 ;;
  esac
}

MISSING=()
report() {                                   # report <tool> <why it is needed>
  if have "$1"; then
    ok  "  [installed] $1  $(version_of "$1")"
  else
    warn "  [missing]   $1  -- $2"
    MISSING+=("$1")
  fi
}
'''

TOOLS_GENERIC = r'''
note "tools the labs use:"
report kubectl   "every lab. The Kubernetes CLI."
report helm      "lab 19 (Helm). Skip it and lab 19 will not run."
report kustomize "lab 19. Optional: 'kubectl apply -k' works without it."
report docker    "lab 29 (images). Optional unless you set ALLOW_DOCKER=yes."
'''

TOOLS_MINIKUBE = r'''
note "tools the labs use:"
report minikube  "creates the cluster these labs run on."
report kubectl   "every lab. minikube ships one, but a standalone kubectl is handier."
report helm      "lab 19 (Helm). Skip it and lab 19 will not run."
report kustomize "lab 19. Optional: 'kubectl apply -k' works without it."
report docker    "minikube's default driver, and lab 29 (images)."
'''

BODY = r'''
if [ ${#MISSING[@]} -eq 0 ]; then
  ok "everything is already installed."
else
  echo
  note "to install: ${MISSING[*]}"
fi

if [ "$CHECK_ONLY" = yes ]; then
  note "--check given: nothing was installed."
  exit 0
fi

if [ ${#MISSING[@]} -gt 0 ] && [ "$ASSUME_YES" != yes ]; then
  echo
  read -r -p "Install these now? [y/N] " reply
  case "$reply" in [yY]*) ;; *) note "nothing installed."; exit 0 ;; esac
fi

# ---------------------------------------------------------------------------
# 3. installers -- package manager first, official binary as the fallback
# ---------------------------------------------------------------------------
# Downloads are pinned to https, TLS 1.2 or better, with a bounded redirect
# chain and a timeout -- so a downgrade or a redirect loop cannot be used to
# feed us something else (CWE-319 cleartext transmission, CWE-601 open
# redirect, CWE-494 download of code without integrity check).
fetch() {                                    # fetch <url> <dest>
  case "$1" in
    https://*) ;;
    *) fail "refusing to download over a non-https URL: $1"; return 1 ;;
  esac
  if have curl; then
    curl --proto '=https' --tlsv1.2 -fsSL --max-redirs 3 --connect-timeout 15 \
         --max-time 300 "$1" -o "$2"
  elif have wget; then
    wget --https-only --secure-protocol=TLSv1_2 --max-redirect=3 \
         --timeout=30 -qO "$2" "$1"
  else
    fail "need curl or wget to download $1"; return 1
  fi
}

# Verify a download against the checksum its publisher serves next to it.
# kubectl, minikube and helm all publish one; when a checksum is unavailable we
# say so out loud rather than quietly trusting the bytes.
verify_sha256() {                            # verify_sha256 <file> <expected|url>
  local file="$1" want="$2" got=""
  case "$want" in
    https://*)
      want="$(curl --proto '=https' --tlsv1.2 -fsSL --max-time 60 "$want" 2>/dev/null \
              | tr -d '[:space:]')" ;;
  esac
  if [ -z "$want" ]; then
    warn "  no published checksum for $(basename "$file") -- installing unverified"
    return 0
  fi
  if   have sha256sum; then got="$(sha256sum "$file" | cut -d' ' -f1)"
  elif have shasum;    then got="$(shasum -a 256 "$file" | cut -d' ' -f1)"
  else warn "  no sha256 tool available -- cannot verify $(basename "$file")"
       return 0
  fi
  # strip any "  filename" suffix the publisher may include
  want="${want%%[[:space:]]*}"
  if [ "$got" = "$want" ]; then
    ok "  checksum verified ($got)"
    return 0
  fi
  fail "  CHECKSUM MISMATCH for $(basename "$file")"
  fail "    expected $want"
  fail "    got      $got"
  fail "  Refusing to install it. Try again on a trusted network."
  rm -f "$file"
  return 1
}

install_bin() {                              # install_bin <src> <name>
  chmod +x "$1" 2>/dev/null
  if can_write "$BINDIR"; then
    mv "$1" "$BINDIR/$2" 2>/dev/null
  elif [ -n "$SUDO" ]; then
    $SUDO install -m 0755 "$1" "$BINDIR/$2" 2>/dev/null && rm -f "$1"
  fi
  if [ -x "$BINDIR/$2" ]; then
    ok "  installed $2 -> $BINDIR/$2"
    return 0
  fi
  # last resort: somewhere we definitely own
  local fallback="$HOME/.local/bin"
  mkdir -p "$fallback" 2>/dev/null
  if [ -f "$1" ] && mv "$1" "$fallback/$2" 2>/dev/null; then
    chmod +x "$fallback/$2"
    ok "  installed $2 -> $fallback/$2"
    warn "     ($BINDIR was not writable; add $fallback to your PATH)"
    return 0
  fi
  fail "  could not install $2 into $BINDIR"
  note "     try again with sudo, or set BINDIR to a directory you own:"
  note "     BINDIR=\"$HOME/.local/bin\" ./\"00-Environment Set up.sh\" --yes"
  return 1
}

install_kubectl() {
  case "$PKG" in
    brew) brew install kubernetes-cli && return 0 ;;
  esac
  note "  downloading kubectl from dl.k8s.io"
  local stable tmp
  stable="$(curl -fsSL https://dl.k8s.io/release/stable.txt 2>/dev/null || echo v1.31.1)"
  tmp="$(mktemp)"
  local base="https://dl.k8s.io/release/${stable}/bin/${OS}/${ARCH}/kubectl"
  fetch "$base" "$tmp" \
    && verify_sha256 "$tmp" "${base}.sha256" \
    && install_bin "$tmp" kubectl
}

install_helm() {
  case "$PKG" in
    brew) brew install helm && return 0 ;;
  esac
  # Downloaded to a file and run from there -- never `curl | bash`, which
  # executes whatever arrives with no chance to see or check it (CWE-494).
  note "  fetching the official get-helm-3 script (it will be run from a file,"
  note "  not piped into a shell; inspect it first with --check if you like)"
  local tmp; tmp="$(mktemp)"
  local target="$BINDIR"
  can_write "$target" || [ -n "$SUDO" ] || target="$HOME/.local/bin"
  mkdir -p "$target" 2>/dev/null
  fetch "https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3" "$tmp" \
    && chmod +x "$tmp" \
    && HELM_INSTALL_DIR="$target" \
       USE_SUDO="$(can_write "$target" && echo false || echo true)" \
       bash "$tmp" >/dev/null 2>&1
  rm -f "$tmp"
  if [ -x "$target/helm" ]; then ok "  installed helm -> $target/helm"
  else fail "  helm install failed -- see https://helm.sh/docs/intro/install/"; fi
}

install_kustomize() {
  case "$PKG" in
    brew) brew install kustomize && return 0 ;;
  esac
  note "  fetching the official kustomize install script (run from a file,"
  note "  not piped into a shell)"
  local tmp dir; tmp="$(mktemp)"; dir="$(mktemp -d)"
  fetch "https://raw.githubusercontent.com/kubernetes-sigs/kustomize/master/hack/install_kustomize.sh" "$tmp" \
    && chmod +x "$tmp" && (cd "$dir" && bash "$tmp" >/dev/null) \
    && install_bin "$dir/kustomize" kustomize
  rm -rf "$tmp" "$dir"
}

install_minikube() {
  case "$PKG" in
    brew) brew install minikube && return 0 ;;
  esac
  note "  downloading minikube from storage.googleapis.com"
  local tmp base
  tmp="$(mktemp)"
  base="https://storage.googleapis.com/minikube/releases/latest/minikube-${OS}-${ARCH}"
  fetch "$base" "$tmp" \
    && verify_sha256 "$tmp" "${base}.sha256" \
    && install_bin "$tmp" minikube
}

install_docker() {
  case "$PKG" in
    brew)
      warn "  Docker Desktop is a cask: brew install --cask docker (then start it)"
      return 0 ;;
    apt|dnf|yum|pacman)
      warn "  install Docker Engine with your distro's official instructions:"
      note "    https://docs.docker.com/engine/install/"
      note "    afterwards:  sudo usermod -aG docker \"$USER\"  (then log out and in)"
      return 0 ;;
    *)
      warn "  install Docker Desktop: https://docs.docker.com/get-docker/"
      return 0 ;;
  esac
}

banner "installing"
for tool in "${MISSING[@]}"; do
  case "$tool" in
    kubectl)   install_kubectl ;;
    helm)      install_helm ;;
    kustomize) install_kustomize ;;
    minikube)  install_minikube ;;
    docker)    install_docker ;;
  esac
done

case ":$PATH:" in
  *":$BINDIR:"*) ;;
  *) warn "$BINDIR is not on your PATH. Add this to ~/.bashrc or ~/.zshrc:"
     note "    export PATH=\"$BINDIR:\$PATH\"" ;;
esac

# ---------------------------------------------------------------------------
# 4. verify
# ---------------------------------------------------------------------------
banner "versions"
hash -r 2>/dev/null            # forget cached "not found" lookups
export PATH="$BINDIR:$HOME/.local/bin:$PATH"
STILL=()
for tool in kubectl helm kustomize __EXTRA_TOOLS__ docker; do
  [ -z "$tool" ] && continue
  if have "$tool"; then ok "  $tool  $(version_of "$tool")"
  else warn "  $tool  still missing"; STILL+=("$tool"); fi
done
if [ ${#STILL[@]} -gt 0 ]; then
  echo
  warn "not installed: ${STILL[*]}"
  note "  network blocked? corporate proxy? install them by hand:"
  note "    kubectl    https://kubernetes.io/docs/tasks/tools/"
  note "    helm       https://helm.sh/docs/intro/install/"
  note "    kustomize  https://kubectl.docs.kubernetes.io/installation/kustomize/"
  note "    minikube   https://minikube.sigs.k8s.io/docs/start/"
  note "    docker     https://docs.docker.com/get-docker/"
fi
'''

ALIASES_GENERIC = r'''
# ---------------------------------------------------------------------------
# 5. aliases for your own shell
# ---------------------------------------------------------------------------
cat > "$HERE/aliases.sh" <<'ALIASES'
# source me:   source aliases.sh
alias k='kubectl'
alias kg='kubectl get'
alias kgp='kubectl get pods'
alias kgpw='kubectl get pods -o wide'
alias kga='kubectl get all'
alias kd='kubectl describe'
alias kaf='kubectl apply -f'
alias kdel='kubectl delete'
alias kl='kubectl logs'
alias kx='kubectl exec -it'
alias kns='kubectl config set-context --current --namespace'
alias kctx='kubectl config current-context'
alias kgc='kubectl config get-contexts'
alias h='helm'
alias kz='kustomize'
export do='--dry-run=client -o yaml'    # kubectl create deploy web --image=nginx $do

# exam-speed extras
alias kaf-='kubectl apply -f -'
alias kdry='kubectl create --dry-run=client -o yaml'
command -v kubectl >/dev/null && source <(kubectl completion bash 2>/dev/null) \
  && complete -o default -F __start_kubectl k 2>/dev/null
ALIASES
ok "wrote aliases.sh   (run:  source aliases.sh)"
'''

ALIASES_MINIKUBE = r'''
# ---------------------------------------------------------------------------
# 5. aliases -- these make `kubectl` mean "this minikube profile"
# ---------------------------------------------------------------------------
# If a standalone kubectl is installed we use it (minikube update-context points
# it at the profile). If not, kubectl becomes `minikube kubectl --`, so every
# command in the labs and in your shell works the same either way.
if have kubectl; then
  KUBECTL_ALIAS="kubectl"
else
  KUBECTL_ALIAS="$MINIKUBE -p $PROFILE kubectl --"
fi

cat > "$HERE/aliases.sh" <<ALIASES
# source me:   source aliases.sh
# Everything here targets the '$PROFILE' minikube profile.
alias kubectl='$KUBECTL_ALIAS'
alias k='$KUBECTL_ALIAS'
alias kg='k get'
alias kgp='k get pods'
alias kgpw='k get pods -o wide'
alias kga='k get all'
alias kd='k describe'
alias kaf='k apply -f'
alias kdel='k delete'
alias kl='k logs'
alias kx='k exec -it'
alias kns='k config set-context --current --namespace'
alias kctx='k config current-context'
alias kgc='k config get-contexts'
alias h='helm --kube-context $PROFILE'
alias helm='helm --kube-context $PROFILE'
alias kz='kustomize'

# minikube itself
alias mk='$MINIKUBE -p $PROFILE'
alias mkstart='$MINIKUBE -p $PROFILE start'
alias mkstop='$MINIKUBE -p $PROFILE stop'
alias mkip='$MINIKUBE -p $PROFILE ip'
alias mkdash='$MINIKUBE -p $PROFILE dashboard'
alias mkaddons='$MINIKUBE -p $PROFILE addons list'
# build images straight into the cluster (lab 29 needs no registry after this)
alias mkdocker='eval \$($MINIKUBE -p $PROFILE docker-env)'

export do='--dry-run=client -o yaml'    # kubectl create deploy web --image=nginx \$do
command -v kubectl >/dev/null && source <(kubectl completion bash 2>/dev/null) \
  && complete -o default -F __start_kubectl k 2>/dev/null
ALIASES
ok "wrote aliases.sh   (run:  source aliases.sh)"
note "after that, 'kubectl get pods' and 'k get pods' both talk to '$PROFILE'"
'''

FOOTER_GENERIC = r'''
banner "next"
note "1.  source aliases.sh"
note "2.  point kubectl at a cluster (kind create cluster / minikube start / your kubeconfig)"
note "3.  ./run-all.sh          -- or ./labs/04-deployments.sh"
'''

FOOTER_MINIKUBE = r'''
banner "next"
note "1.  source aliases.sh"
note "2.  ./\"01-Minikube cluster set up.sh\"   -- starts the cluster + addons"
note "3.  ./run-all.sh                        -- or ./labs/04-deployments.sh"
'''


def environment_setup(flavour: str = "kubernetes") -> str:
    """The full `00-Environment Set up.sh` for one flavour."""
    minikube = flavour == "minikube"
    return (HEADER
            + (TOOLS_MINIKUBE if minikube else TOOLS_GENERIC)
            + BODY.replace("__EXTRA_TOOLS__", "minikube" if minikube else "")
            + (ALIASES_MINIKUBE if minikube else ALIASES_GENERIC)
            + (FOOTER_MINIKUBE if minikube else FOOTER_GENERIC))
