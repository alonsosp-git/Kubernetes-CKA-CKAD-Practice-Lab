# k8s-practice-lab -- runs the browser UI on :8899
#
#   docker build -t k8s-practice-lab .
#   docker run -d --name k8s-lab -p 127.0.0.1:8899:8899 k8s-practice-lab
#   docker logs k8s-lab      # the URL it prints contains the session token
#
# or just:  docker compose up --build
#
# Hardening notes (CIS Docker Benchmark 4.x, NIST SP 800-190):
#   * multi-stage, so no build tooling ships in the runtime image
#   * base image pinned to an exact patch version. Pin it to a digest as well
#     for a release build -- resolve it yourself rather than trusting one
#     copied from a README, and let Dependabot move it (see .github/):
#         docker buildx imagetools inspect python:3.12.11-slim
#     (CWE-1357 reliance on an untrusted component; supply-chain integrity)
#   * runs as a non-root numeric UID with no shell and no home to write to
#   * read-only root filesystem, writable only where the app needs it
#   * no package manager, no curl, no build-essential in the final layer
#   * healthcheck does not need credentials, so it stays on a public route

# ---- build stage: nothing from here reaches the runtime image --------------
FROM python:3.12.11-slim AS build
WORKDIR /src
COPY k8slab/ ./k8slab/
COPY k8s_lab.py ./
COPY labs/ ./labs/
COPY manifests/ ./manifests/
COPY charts/ ./charts/
COPY kustomize/ ./kustomize/
COPY README.md LICENSE NOTICE ./
# Byte-compile once so the runtime image can stay read-only.
RUN python -m compileall -q ./k8slab ./k8s_lab.py

# ---- runtime stage ---------------------------------------------------------
FROM python:3.12.11-slim

LABEL org.opencontainers.image.title="k8s-practice-lab" \
      org.opencontainers.image.description="Kubernetes handbook simulator: kubectl CLI + live topology" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="1.1.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    PIP_NO_CACHE_DIR=1 \
    K8SLAB_IN_DOCKER=1 \
    K8SLAB_STATE=/tmp/k8slab

# 10001 is well outside the host's system-UID range, so a container escape
# maps to an unprivileged, non-existent user on the host (CWE-250).
RUN groupadd --gid 10001 lab \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin lab \
 && install -d -o 10001 -g 10001 /tmp/k8slab

WORKDIR /app
COPY --from=build --chown=root:root --chmod=0555 /src /app

# Drop setuid/setgid bits anywhere in the image: nothing here needs them, and
# their absence removes a whole class of local escalation (CWE-732).
RUN find / -xdev -perm /6000 -type f -exec chmod a-s {} + 2>/dev/null || true

USER 10001:10001
EXPOSE 8899

# Loopback inside the container's own namespace; publish it to the host with
# `-p 127.0.0.1:8899:8899` so it is not exposed to the network by accident.
# /healthz is the one route that needs no session token: it returns nothing
# but "ok", so an unauthenticated caller learns only that the port is alive.
HEALTHCHECK --interval=30s --timeout=4s --start-period=5s --retries=3 \
  CMD ["python","-c","import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8899/healthz',timeout=3).status==200 else 1)"]

ENTRYPOINT ["python", "k8s_lab.py"]
CMD ["--web", "--host", "0.0.0.0", "--port", "8899"]
