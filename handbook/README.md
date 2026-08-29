# handbook/ — intentionally empty

These two directories are where the lab looks for scanned book pages:

    pages/page_NN.png    one image per page
    text/page_NN.txt     the text of that page, for search

**Nothing is shipped in them, and nothing you put in them should ever be
committed.** `.gitignore` covers both.

## Why

This project was written while working through a Kubernetes study guide, and
it can show the relevant page of that guide beside each exercise. That guide is
someone else's copyrighted work. Redistributing its pages — as images, as OCR
text, or as a PDF — is not something a licence to *read* a book allows,
whatever the format. So the pages are not here, and importing them is a local
step you take with your own copy:

    python tools/import_handbook.py "~/path/to/your-book.pdf"

## What you get without it

Everything except the scans. The app ships with:

* an **original vector diagram for all 50 topics** (`k8slab/diagrams.py`),
  drawn for this project — that is what the Page tab shows by default;
* notes, commands, gotchas and interview questions written for this lab;
* 29 lab scripts and ~42 manifests, all original.

The page map below records which page of the source guide inspired each topic,
so the ordering makes sense to anyone following along with a copy. Page numbers
and topic names are facts about a book, not a reproduction of it.

| Pages | Topic |
|------|-------|
| 1–2 | Cover / table of contents |
| 3 | Introduction to Kubernetes |
| 4 | Kubernetes architecture |
| 5 | Pods |
| 6 | Deployments |
| 7 | Services |
| 8 | ReplicaSets |
| 9 | ConfigMaps & Secrets |
| 10 | Namespaces & ResourceQuotas |
| 11 | Persistent Volumes |
| 12 | PersistentVolumeClaims |
| 13 | Storage Classes |
| 14 | Dynamic provisioning |
| 15 | StatefulSets |
| 16 | DaemonSets |
| 17 | Jobs |
| 18 | CronJobs |
| 19 | Init containers |
| 20 | Multi-container pods |
| 21 | Labels & selectors |
| 22 | Annotations |
| 23 | Taints & tolerations |
| 24 | Node affinity |
| 25 | Pod affinity & anti-affinity |
| 26 | Resource requests & limits |
| 27 | Liveness / readiness / startup probes |
| 28 | Horizontal Pod Autoscaler |
| 29 | Vertical Pod Autoscaler |
| 30 | Cluster Autoscaler |
| 31 | Ingress |
| 32 | Ingress Controller |
| 33 | Network Policies |
| 34 | RBAC |
| 35 | Service Accounts |
| 36 | Authentication |
| 37 | Authorization |
| 38 | Admission Controllers |
| 39 | etcd |
| 40 | Scheduler |
| 41 | Controller Manager |
| 42 | Kubelet |
| 43 | kube-proxy |
| 44 | Container runtime |
| 45 | Helm |
| 46 | Kustomize |
| 47 | Operators |
| 48 | Custom Resource Definitions |
| 49 | API Server |
| 50 | Observability |
| 51 | Logging |
| 52 | Troubleshooting |
| 53 | Production best practices |
| 54 | CKA/CKAD questions & cheat sheet |
