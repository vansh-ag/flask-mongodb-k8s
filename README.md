# Flask + MongoDB Application on Kubernetes (Minikube)

A Python Flask REST API backed by MongoDB, containerised with Docker and deployed on a local
Kubernetes cluster (Minikube). The deployment uses a **StatefulSet with persistent storage** for
MongoDB, a **Deployment with 2–5 replicas** for Flask, **internal DNS-based service discovery**,
**Secrets / ConfigMaps** for configuration, **CPU/memory requests and limits**, and a
**Horizontal Pod Autoscaler (HPA)** that was load-tested and observed scaling from 2 to 5 Pods.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Technology Stack](#2-technology-stack)
3. [Architecture](#3-architecture)
4. [Project Structure](#4-project-structure)
5. [Application APIs](#5-application-apis)
6. [Prerequisites](#6-prerequisites)
7. [Local Application Setup](#7-local-application-setup)
8. [Docker Setup](#8-docker-setup)
9. [Kubernetes Deployment (Step by Step)](#9-kubernetes-deployment-step-by-step)
10. [MongoDB StatefulSet](#10-mongodb-statefulset)
11. [Services, Networking and DNS](#11-services-networking-and-dns)
12. [Resource Requests and Limits](#12-resource-requests-and-limits)
13. [Horizontal Pod Autoscaler (HPA)](#13-horizontal-pod-autoscaler-hpa)
14. [API Test Results](#14-api-test-results)
15. [Persistent Storage Test](#15-persistent-storage-test)
16. [MongoDB Probe Optimisation](#16-mongodb-probe-optimisation)
17. [Design Choices and Alternatives](#17-design-choices-and-alternatives)
18. [Security Review](#18-security-review)
19. [Troubleshooting](#19-troubleshooting)
20. [Cleanup](#20-cleanup)
21. [Final Assignment Checklist](#21-final-assignment-checklist)

---

## 1. Project Overview

### Problem statement
A web application that stores data needs more than a running container. The database must keep
its data when containers restart. Credentials must stay out of the code. The application must
find the database reliably even though container IPs change. It must also cope with changing
load without manual intervention.

### Objective
Build a Flask + MongoDB application and deploy it on Kubernetes so that:

- Flask connects to MongoDB using PyMongo and a connection string from the environment.
- The application is containerised with a minimal, non-root Docker image.
- MongoDB runs with **authentication** and **persistent storage** that survives Pod recreation.
- Flask runs with **multiple replicas** and reaches MongoDB through **Kubernetes DNS**.
- Credentials live in a **Secret** and non-sensitive settings in a **ConfigMap**.
- Both workloads have **CPU and memory requests and limits**.
- An **HPA** scales Flask on CPU utilisation, and this is **actually tested**.

### Architecture in brief
The application exposes three HTTP endpoints: `GET /`, `POST /data` and `GET /data`. On
Kubernetes, Flask runs as a horizontally scalable **Deployment** (2–5 Pods), exposed through a
**NodePort** Service. MongoDB runs as a single-replica **StatefulSet** with a
**PersistentVolumeClaim**. It is reachable **only inside the cluster**, through a ClusterIP
Service named `mongodb`.

## 2. Technology Stack

| Layer | Technology | Version (as used) |
|---|---|---|
| Language | Python | 3.11 (`python:3.11-slim`) |
| Web framework | Flask | 3.1.0 |
| MongoDB driver | PyMongo | 4.10.1 |
| Env loading (local dev) | python-dotenv | 1.0.1 |
| Package installer | uv | 0.13.5 |
| Database | MongoDB | 8.0 (`mongo:8.0`) |
| Containers | Docker Desktop | 29.6.2 |
| Local cluster | Minikube (docker driver, containerd runtime) | v1.39.0 |
| Orchestration | Kubernetes | server v1.37.0 |
| CLI | kubectl | v1.37.2 |
| Metrics | Metrics Server (Minikube addon) | |
| Autoscaling | HorizontalPodAutoscaler | `autoscaling/v2` |
| Host OS | Windows 11 (PowerShell) | |

## 3. Architecture

```mermaid
flowchart TB
    user(["User / Client<br/>(browser, curl)"])

    subgraph cluster["Minikube cluster"]
        ms["Metrics Server<br/>(kube-system)"]

        subgraph ns["Namespace: flask-app"]
            fsvc["Service: flask-service<br/>NodePort 30080 → 5000"]
            hpa["HPA: flask-app-hpa<br/>min 2 · max 5 · CPU 70%"]

            subgraph fdep["Deployment: flask-app (2–5 Pods)"]
                f1["Flask Pod"]
                f2["Flask Pod"]
                fn["… up to 5"]
            end

            cm["ConfigMap: flask-app-config<br/>MONGODB_DATABASE · MONGODB_COLLECTION · APP_ENV"]
            sec["Secret: mongodb-secret<br/>root + app credentials"]
            initcm["ConfigMap: mongodb-init-script<br/>mongodb-init.sh"]

            msvc["Service: mongodb<br/>ClusterIP :27017"]
            hsvc["Service: mongodb-headless<br/>clusterIP: None"]

            subgraph sts["StatefulSet: mongodb (1 replica)"]
                m0["Pod: mongodb-0<br/>mongo:8.0"]
            end

            pvc["PVC: mongodb-data-mongodb-0<br/>2Gi · RWO · standard"]
        end

        pv["PersistentVolume<br/>(dynamically provisioned,<br/>minikube-hostpath)"]
    end

    user -->|"HTTP"| fsvc
    fsvc --> f1 & f2 & fn
    f1 & f2 & fn -->|"mongodb://…@mongodb:27017/flask_db"| msvc
    msvc --> m0
    hsvc -. "stable DNS identity" .- m0
    m0 -->|"/data/db"| pvc
    pvc --> pv

    cm -. env .-> fdep
    sec -. env .-> fdep
    cm -. "MONGO_INITDB_DATABASE" .-> m0
    sec -. env .-> m0
    initcm -. "/docker-entrypoint-initdb.d" .-> m0

    ms -->|"Pod CPU metrics"| hpa
    hpa -->|"scales replicas"| fdep
```

**Request flow:** client → `flask-service` (NodePort) → one of the Flask Pods → `mongodb`
(ClusterIP, resolved by Kubernetes DNS) → `mongodb-0` → data on the PersistentVolume.

## 4. Project Structure

```
flask-mongodb-k8s/
├── app.py                          # Flask application (3 endpoints + error handlers)
├── requirements.txt                # Pinned Python dependencies
├── Dockerfile                      # python:3.11-slim + uv, non-root user
├── .dockerignore                   # Keeps .env, .venv, caches out of the image
├── .gitignore                      # Keeps .env, .venv, caches out of Git
├── .env.example                    # Template for local MONGODB_URI (no real secrets)
├── README.md                       # This document
└── k8s/
    ├── configmap.yaml              # ConfigMap flask-app-config
    ├── mongodb-init-configmap.yaml # ConfigMap mongodb-init-script (creates app user)
    ├── mongodb-service.yaml        # Services mongodb-headless + mongodb (ClusterIP)
    ├── mongodb-statefulset.yaml    # StatefulSet mongodb + volumeClaimTemplates
    ├── flask-deployment.yaml       # Deployment flask-app (2 replicas)
    ├── flask-service.yaml          # Service flask-service (NodePort 30080)
    └── flask-hpa.yaml              # HPA flask-app-hpa (2–5 replicas, CPU 70%)
```

`.env` (real local credentials) and `.venv` (local virtual environment) exist only on the
developer machine and are excluded from both Git and the Docker image.

The Namespace and the Secret are created with `kubectl` commands (see Section 9), so that no
credentials are ever written into a file.

## 5. Application APIs

Base URL: `http://<host>:<port>`. See [Section 11](#11-services-networking-and-dns) for how to
reach it on Minikube.

### `GET /`
Returns plain text.
```
200 OK
Welcome to the Flask app! The current time is: 2026-11-11 20:46:28
```
> The container clock is UTC, so the time shown is UTC.

### `POST /data`
Stores one JSON **object** in the `data` collection of `flask_db`.

```powershell
Invoke-RestMethod http://localhost:8080/data -Method Post -ContentType "application/json" `
  -Body '{"name": "Final Kubernetes Audit", "message": "Testing Flask and MongoDB integration", "source": "final-audit"}'
```
```bash
curl -X POST http://localhost:8080/data -H "Content-Type: application/json" \
  -d '{"name": "Final Kubernetes Audit", "message": "Testing Flask and MongoDB integration", "source": "final-audit"}'
```

| Situation | Status | Response body |
|---|---|---|
| Valid, non-empty JSON object | `201` | `{"status": "Data inserted"}` |
| Body is not valid JSON (or missing `Content-Type: application/json`) | `400` | `{"error": "Request body must be valid JSON"}` |
| JSON is not an object (e.g. an array) | `400` | `{"error": "JSON body must be an object, e.g. {\"name\": \"value\"}"}` |
| Empty object `{}` | `400` | `{"error": "JSON object must not be empty"}` |
| MongoDB unreachable / database error | `503` | `{"error": "Database error, could not save data"}` |

### `GET /data`
Returns a JSON array of all documents, without `_id`.
```json
[{"message": "Testing Flask and MongoDB integration", "name": "Final Kubernetes Audit", "source": "final-audit"}]
```
A database error returns `503 {"error": "Database error, could not read data"}`.

### Other responses
| Situation | Status | Body |
|---|---|---|
| Unknown route | `404` | `{"error": "Endpoint not found"}` |
| Wrong method (e.g. `PUT /data`) | `405` | `{"error": "Method not allowed"}` |

### Configuration (environment variables)

| Variable | Required | Default | Source on Kubernetes |
|---|---|---|---|
| `MONGODB_URI` | **Yes** (app refuses to start without it) | none | Built at container start from Secret values |
| `MONGODB_DATABASE` | No | `flask_db` | ConfigMap `flask-app-config` (read by image 1.1; image 1.0 in Minikube uses the same value hardcoded, see Section 8) |
| `MONGODB_COLLECTION` | No | `data` | ConfigMap `flask-app-config` (read by image 1.1; image 1.0 in Minikube uses the same value hardcoded) |
| `APP_ENV` | No | none | ConfigMap `flask-app-config` (informational; not read by `app.py`) |

## 6. Prerequisites

- Windows 11 with **Docker Desktop** running
- **Minikube** and **kubectl** installed
- **Python 3.11** and **uv** (for local development only)
- Metrics Server addon enabled in Minikube (needed for the HPA)
- Optional: a Docker Hub account (only if you want to push the image to a registry)

## 7. Local Application Setup

Run these in PowerShell from the project directory.

```powershell
# 1. Create a virtual environment and install dependencies with uv
uv venv --python 3.11
.\.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt

# 2. Create your local environment file from the template
Copy-Item .env.example .env
#    Then edit .env and set MONGODB_URI to your local MongoDB, for example:
#    MONGODB_URI=mongodb://<username>:<password>@localhost:27017/?authSource=admin

# 3. Start the app
python app.py
```

The app listens on `http://localhost:5000`. To start a local MongoDB, one option is a throwaway
container. Choose your own credentials and use the same ones in `.env`:

```powershell
docker run -d --name local-mongo -p 27017:27017 `
  -e MONGO_INITDB_ROOT_USERNAME=<username> -e MONGO_INITDB_ROOT_PASSWORD=<password> mongo:8.0
```

> `authSource=admin` is correct for a local root user. On Kubernetes the app uses a dedicated
> application user stored in `flask_db`, so the cluster URI uses `authSource=flask_db`.

## 8. Docker Setup

### Dockerfile summary

| Item | Value |
|---|---|
| Base image | `python:3.11-slim` |
| uv installation | `COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /bin/` (official Astral image, pinned) |
| Dependency install | `uv pip install --system --no-cache -r requirements.txt` |
| Layer caching | `requirements.txt` is copied and installed **before** `app.py` |
| Runtime user | `appuser` (UID 1000, non-root) |
| Exposed port | `5000` |
| Start command | `CMD ["python", "app.py"]` |
| Environment | `PYTHONDONTWRITEBYTECODE=1`, `PYTHONUNBUFFERED=1` (logs appear immediately) |
| Credentials | **None** in the image; `MONGODB_URI` is supplied at runtime |

The image contains only `/app/app.py` and `/app/requirements.txt`. This was verified inside a
running Pod: `.env` and `.venv` are **absent**, and the process runs as UID 1000 (`appuser`).

### Image versions

| Tag | Built from | Used by | Notes |
|---|---|---|---|
| `flask-mongodb-app:1.0` | `app.py` **before** the database/collection names were made configurable | **Minikube deployment** (loaded with `minikube image load`) | Uses `flask_db` / `data` **hardcoded**. These are the same values as the ConfigMap, so behaviour is identical, but this image ignores `MONGODB_DATABASE` / `MONGODB_COLLECTION` |
| `flask-mongodb-app:1.1` | **current** source (`app.py` reads `MONGODB_DATABASE` / `MONGODB_COLLECTION`) | Local Docker test, Docker Hub | `app.py` inside the image matches the project file (SHA-256 verified) |

The running Kubernetes Pods were confirmed to contain the 1.0 `app.py`. The cluster was
deliberately left on 1.0. To move it to 1.1 you would run `minikube image load
flask-mongodb-app:1.1`, change `image:` in `k8s/flask-deployment.yaml`, and re-apply it.

### Build the image

```powershell
docker build -t flask-mongodb-app:1.1 .
```

Observed: the build succeeded, giving image ID `sha256:f6c63e5fbf4c…` with user `appuser`,
`CMD ["python", "app.py"]` and port `5000/tcp`. The image contains only `/app/app.py` and
`/app/requirements.txt`, with no `.env`.

### Local Docker test (independent of Kubernetes)

The container needs `MONGODB_URI` at runtime. Inside a container, `localhost` means the container
itself, so `host.docker.internal` is used to reach a MongoDB published on the host. The test
used a **separate, temporary, unauthenticated** MongoDB container on host port 27018. It did not
use the Kubernetes MongoDB or its PVC.

```powershell
# Temporary MongoDB for the test
docker run -d --name flask-docker-test-mongodb -p 27018:27017 mongo:8.0

# Flask container (database/collection default to flask_db / data)
docker run -d --name flask-docker-test -p 5001:5000 `
  -e MONGODB_URI="mongodb://host.docker.internal:27018/" flask-mongodb-app:1.1

docker logs flask-docker-test     # shows: Running on all addresses (0.0.0.0)
```

Observed results (`http://localhost:5001`):

| Test | Request | Status | Response |
|---|---|---|---|
| A | `GET /` | **200** | `Welcome to the Flask app! The current time is: 2026-11-11 21:03:40` |
| B | `POST /data` `{"name": "Docker Container Test", "message": "Independent Docker deployment verified", "source": "docker-local"}` | **201** | `{"status":"Data inserted"}` |
| C | `GET /data` | **200** | `[{"message":"Independent Docker deployment verified","name":"Docker Container Test","source":"docker-local"}]` |
| D1 | `POST /data` `{}` | **400** | `{"error":"JSON object must not be empty"}` |
| D2 | `POST /data` invalid JSON | **400** | `{"error":"Request body must be valid JSON"}` |
| D3 | `POST /data` `[1, 2, 3]` | **400** | `{"error":"JSON body must be an object, e.g. {\"name\": \"value\"}"}` |

The document was confirmed in `flask_db.data` of the **temporary** MongoDB. Both test containers
were removed afterwards (`docker rm -f flask-docker-test flask-docker-test-mongodb`).

> To test against an authenticated local MongoDB instead, put the credentials in the URI, e.g.
> `mongodb://<username>:<password>@host.docker.internal:27017/?authSource=admin`.

### Load the image into Minikube

```powershell
minikube image load flask-mongodb-app:1.0
minikube image ls | Select-String flask-mongodb-app
```

`minikube image load` copies the image from Docker Desktop into Minikube's own container runtime
(containerd). The Deployment uses `imagePullPolicy: IfNotPresent`, so Kubernetes uses this local
copy, and **no public registry is needed** for the Minikube deployment.

### Push to Docker Hub

```powershell
docker login
docker tag flask-mongodb-app:1.1 <dockerhub-username>/flask-mongodb-app:1.1
docker push <dockerhub-username>/flask-mongodb-app:1.1
```

**Status: NOT VERIFIED (pending).** Docker Hub authentication and the destination username have
not been confirmed yet, so nothing has been pushed.

To deploy from Docker Hub instead, change `image:` in `k8s/flask-deployment.yaml` to
`<dockerhub-username>/flask-mongodb-app:1.1`. The current Minikube deployment does **not** use a
registry. It runs the locally loaded `flask-mongodb-app:1.0`.

## 9. Kubernetes Deployment (Step by Step)

The order matters. The namespace must exist first, and the Secret and ConfigMaps must exist
before the Pods that reference them start.

### Step 1: Start Minikube and enable Metrics Server
```powershell
minikube start
minikube addons enable metrics-server
kubectl get nodes
```

### Step 2: Create the namespace
```powershell
kubectl create namespace flask-app
```

### Step 3: Apply the application ConfigMap
```powershell
kubectl apply -f k8s/configmap.yaml
```
`flask-app-config` contains `MONGODB_DATABASE=flask_db`, `MONGODB_COLLECTION=data` and
`APP_ENV=production`.

### Step 4: Create the Secret (no credentials in any file)
The commands below generate random 32-character hex passwords in PowerShell, so the passwords
are never typed or saved to disk:

```powershell
$rootPass = [guid]::NewGuid().ToString("N")
$appPass  = [guid]::NewGuid().ToString("N")

kubectl create secret generic mongodb-secret -n flask-app `
  --from-literal=MONGO_INITDB_ROOT_USERNAME=<root-username> `
  --from-literal=MONGO_INITDB_ROOT_PASSWORD=$rootPass `
  --from-literal=MONGODB_APP_USERNAME=flask_user `
  --from-literal=MONGODB_APP_PASSWORD=$appPass

Remove-Variable rootPass, appPass
```

| Secret key | Used by | Purpose |
|---|---|---|
| `MONGO_INITDB_ROOT_USERNAME` / `_PASSWORD` | MongoDB | Root (admin) user, created by the official image on first start; enables auth |
| `MONGODB_APP_USERNAME` / `_PASSWORD` | MongoDB init script **and** Flask | Application user with `readWrite` on `flask_db` only |

Verify the key names without revealing values:
```powershell
kubectl describe secret mongodb-secret -n flask-app
```

> The app password is inserted into a URI without URL-encoding, so it should contain only
> URL-safe characters. Hex GUIDs, as generated above, are safe.

### Step 5: Apply the MongoDB init ConfigMap
```powershell
kubectl apply -f k8s/mongodb-init-configmap.yaml
```

### Step 6: Apply the MongoDB Services
```powershell
kubectl apply -f k8s/mongodb-service.yaml
```

### Step 7: Apply the MongoDB StatefulSet
```powershell
kubectl apply -f k8s/mongodb-statefulset.yaml
```

### Step 8: Wait for MongoDB to be ready
```powershell
kubectl wait --for=condition=Ready pod/mongodb-0 -n flask-app --timeout=300s
kubectl logs mongodb-0 -n flask-app | Select-String mongodb-init
```
Expected init output on the **first** start:
```
mongodb-init: setting up user 'flask_user' on database 'flask_db'
mongodb-init: user created
mongodb-init: done
```

### Step 9: Apply the Flask Deployment
```powershell
kubectl apply -f k8s/flask-deployment.yaml
kubectl rollout status deployment/flask-app -n flask-app
```

### Step 10: Apply the Flask Service
```powershell
kubectl apply -f k8s/flask-service.yaml
```

### Step 11: Apply the HPA
```powershell
kubectl apply -f k8s/flask-hpa.yaml
```

### Check everything
```powershell
kubectl get all,pvc,configmap,hpa -n flask-app
kubectl get pv
```

Tip: validate any manifest against the API server before applying it:
```powershell
kubectl apply --dry-run=server -f k8s/flask-deployment.yaml
```

## 10. MongoDB StatefulSet

### Configuration (`k8s/mongodb-statefulset.yaml`)

| Setting | Value |
|---|---|
| Name / replicas | `mongodb` / 1 |
| Image | `mongo:8.0` |
| `serviceName` | `mongodb-headless` |
| Labels / selector | `app: mongodb` |
| Authentication | Enabled via `MONGO_INITDB_ROOT_USERNAME` / `MONGO_INITDB_ROOT_PASSWORD` from the Secret |
| App user variables | `MONGODB_APP_USERNAME` / `MONGODB_APP_PASSWORD` from the Secret |
| Database variable | `MONGO_INITDB_DATABASE`, mapped from ConfigMap key `MONGODB_DATABASE` via `configMapKeyRef` |
| Data volume | PVC template `mongodb-data` mounted at `/data/db` |
| Init script | ConfigMap `mongodb-init-script` mounted **read-only** at `/docker-entrypoint-initdb.d/` |
| Resources | requests 200m / 250Mi, limits 500m / 500Mi |

### Why a StatefulSet
A database needs a stable identity and its **own** storage. A StatefulSet always names the Pod
`mongodb-0`, gives it a stable DNS name through the headless Service, and re-attaches the
**same** PVC whenever the Pod is recreated. A Deployment gives Pods random names and has no
per-Pod storage.

### Authentication and the dedicated application user
Setting `MONGO_INITDB_ROOT_USERNAME` / `_PASSWORD` makes the official image create a root user
and **enable authentication**. Flask does **not** use root. It uses a separate **application
user** (`flask_user`) with only `readWrite` on `flask_db`.

### Initialisation script
The official `mongo` image runs scripts in `/docker-entrypoint-initdb.d/` **only when `/data/db`
is empty** (the first start). `mongodb-init.sh`:

1. Checks that all required variables are set (fails with a clear message otherwise).
2. Logs in as root through `mongosh` on `127.0.0.1`.
3. Creates the application user in `flask_db` with role `readWrite` on `flask_db`, and skips
   creation if the user already exists.

Credentials are read inside `mongosh` with `process.env`, so they are never part of the script
text. The script body runs in a subshell `( … )` so that `set -e` cannot leak into the image's
entrypoint.

### Persistent volume and `/data/db`
- `volumeClaimTemplates` creates PVC **`mongodb-data-mongodb-0`** (template name + Pod name):
  `ReadWriteOnce`, StorageClass `standard`, `2Gi`.
- Minikube's default StorageClass `standard` (provisioner `k8s.io/minikube-hostpath`)
  **dynamically creates the PersistentVolume**. No manual PV is defined.
- Observed: PVC `Bound` to PV `pvc-f192e6d3-2749-4797-8f2a-7e78c75ce25b` (2Gi, RWO, reclaim policy
  `Delete`). The PV is stored on the Minikube node at
  `/tmp/hostpath-provisioner/flask-app/mongodb-data-mongodb-0`.
- MongoDB stores all its data files in `/data/db`, so the data lives on the PV, not in the
  container. This is proven in [Section 15](#15-persistent-storage-test).

> **Note:** Minikube's hostpath provisioner does not enforce the 2Gi size. `df` inside the Pod
> shows the node's disk size. Real cloud storage classes do enforce it.

### Probes and readiness verification
All probes check MongoDB on the **Pod IP**, not `localhost`. During initialisation the image runs
a temporary `mongod` bound to `127.0.0.1` only, so a `localhost` check would report "healthy" too
early.

| Probe | Handler | Check | Timing |
|---|---|---|---|
| startupProbe | `tcpSocket` port 27017 | MongoDB accepts TCP connections on the Pod IP | every 5s, timeout 10s, up to 30 failures (≈150s) |
| readinessProbe | `exec` `mongosh` (uses `POD_IP` from the Downward API) | **authenticated** `ping` as the app user on `flask_db` | every 10s, timeout 10s, 3 failures |
| livenessProbe | `tcpSocket` port 27017 | MongoDB still accepts TCP connections | every 20s, timeout 10s, 3 failures, then restart |

The readiness probe proves that the app user can **log in** and use `flask_db`. Only then does
the `mongodb` Service send traffic to the Pod. Why startup and liveness use TCP is explained in
[Section 16](#16-mongodb-probe-optimisation).

## 11. Services, Networking and DNS

### Services

| Service | Type | Port | Purpose |
|---|---|---|---|
| `flask-service` | **NodePort** | 5000 → 5000, **nodePort 30080** | External access to Flask; load-balances across all Flask Pods |
| `mongodb` | **ClusterIP** | 27017 → 27017 | Stable internal virtual IP (observed `10.110.199.51`) used by Flask |
| `mongodb-headless` | **Headless** (`clusterIP: None`) | 27017 | Stable per-Pod DNS name for the StatefulSet |

Only `flask-service` is NodePort. Neither MongoDB Service is NodePort or LoadBalancer, so MongoDB
is **not reachable from outside the cluster**.

### Flask Deployment (`k8s/flask-deployment.yaml`)

| Setting | Value |
|---|---|
| Name / replicas | `flask-app` / 2 (then managed by the HPA, 2–5) |
| Labels / selector | `app: flask-app` |
| Image / pull policy | `flask-mongodb-app:1.0` / `IfNotPresent` |
| Port | 5000 |
| ConfigMap env | `MONGODB_DATABASE`, `MONGODB_COLLECTION`, `APP_ENV` (via `configMapKeyRef`) |
| Secret env | `MONGODB_APP_USERNAME`, `MONGODB_APP_PASSWORD` (via `secretKeyRef`) |
| Resources | requests 200m / 250Mi, limits 500m / 500Mi |
| Probes | HTTP `GET /` on 5000: startup (5s × 30), readiness (10s × 3), liveness (20s × 3) |

**How Flask gets `MONGODB_URI`.** The container command is:

```sh
sh -c 'export MONGODB_URI="mongodb://${MONGODB_APP_USERNAME}:${MONGODB_APP_PASSWORD}@mongodb:27017/flask_db?authSource=flask_db" && exec python app.py'
```

- The values come from the Secret at runtime, so nothing is hardcoded in YAML and nothing is
  echoed to the logs.
- `exec` replaces the shell with Python, so Python is PID 1 and receives `SIGTERM` directly for a
  clean shutdown. This was verified: `/proc/1/cmdline` is `python app.py`.
- The URI points at the **Service name `mongodb`**, never at a Pod IP.

### Accessing Flask from Windows (Minikube docker driver)
The node IP is shown by `minikube ip` (observed `192.168.49.2`), so the NodePort URL is
`http://192.168.49.2:30080`. With Minikube's **docker driver on Windows**, the node network is
usually not directly reachable from the host, so use one of these:

```powershell
# Option A: Minikube tunnel to the NodePort service (keep this terminal open)
minikube service flask-service -n flask-app --url

# Option B: kubectl port-forward (keep this terminal open), then use http://localhost:8080
kubectl port-forward svc/flask-service 8080:5000 -n flask-app
```

The NodePort itself was verified from inside the Minikube node:
`minikube ssh -- curl -s http://localhost:30080/` returned HTTP 200, and so did `/data`.

### Kubernetes DNS
Every cluster runs a DNS server (CoreDNS, Service `kube-dns`, observed at `10.96.0.10`). It
publishes a DNS record for every Service. Each Pod's `/etc/resolv.conf` points to it, so
applications find Services **by name** instead of by IP.

### Namespace-based service discovery: short vs fully qualified names
A Service's **fully qualified** name is `<service>.<namespace>.svc.cluster.local`. Each Pod's
resolver has search domains for its own namespace. Observed in a Flask Pod:

```
search flask-app.svc.cluster.local svc.cluster.local cluster.local
nameserver 10.96.0.10
```

So, from a Pod in `flask-app`, the **short** name `mongodb` expands to
`mongodb.flask-app.svc.cluster.local`. A Pod in a different namespace would have to use
`mongodb.flask-app` or the full name.

### Observed DNS test (from both Flask Pods, final audit)

```powershell
kubectl exec deploy/flask-app -n flask-app -- python -c "import socket; print(socket.gethostbyname('mongodb')); print(socket.gethostbyname('mongodb.flask-app.svc.cluster.local'))"
```

| Name | Resolved to | Meaning |
|---|---|---|
| `mongodb` | `10.110.199.51` | = ClusterIP of Service `mongodb` ✔ |
| `mongodb.flask-app.svc.cluster.local` | `10.110.199.51` | same Service, fully qualified ✔ |
| `mongodb-headless` | `10.244.0.13` | headless: returns the **Pod IP** of `mongodb-0` |
| `mongodb-0.mongodb-headless.flask-app.svc.cluster.local` | `10.244.0.13` | stable per-Pod DNS name |

> These IPs were observed during testing. Kubernetes assigns them, and they **can change when
> resources are recreated**.

### Why Flask uses `mongodb` instead of a Pod IP
Pod IPs are temporary. In the persistence tests, recreating `mongodb-0` changed its IP each time
(`10.244.0.4` → `10.244.0.11` in the first test, `10.244.0.12` → `10.244.0.13` in the final test).
The Service IP `10.110.199.51` and the name `mongodb` stayed the same. The Flask Pods kept
working with **0 restarts**, because PyMongo reconnected to the same Service name.

### ClusterIP vs headless Service
- **`mongodb` (ClusterIP):** one stable virtual IP. kube-proxy forwards traffic to a **Ready**
  backing Pod, and clients don't need to know which Pod they reach. Flask uses this Service.
- **`mongodb-headless` (`clusterIP: None`):** no virtual IP. DNS returns Pod IPs directly and
  creates a stable per-Pod name (`mongodb-0.mongodb-headless…`). A StatefulSet requires it
  (`serviceName`) to give each Pod a stable network identity. That matters most for MongoDB
  replica sets, which this project does not use.

### How Flask communicates with MongoDB
`Flask Pod → DNS lookup "mongodb" → 10.110.199.51 (ClusterIP) → kube-proxy → mongodb-0:27017 →
authenticate as flask_user (authSource=flask_db) → read/write flask_db.data`

## 12. Resource Requests and Limits

Both workloads use exactly the assignment values. This was verified on the Deployment and
StatefulSet specs **and** on every running Pod:

| Workload | CPU request | CPU limit | Memory request | Memory limit |
|---|---:|---:|---:|---:|
| Flask (`flask-app`, each Pod) | 200m | 500m | 250Mi | 500Mi |
| MongoDB (`mongodb`) | 200m | 500m | 250Mi | 500Mi |

- **Requests** are the resources reserved for a container. The scheduler only places a Pod on a
  node with at least that much unreserved CPU and memory, so requests drive **scheduling** and
  protect Pods from noisy neighbours.
- **Limits** are the maximum a container may use.
  - **CPU** above the limit is **throttled** (slowed down, not killed).
  - **Memory** above the limit gets the container **killed (`OOMKilled`, exit code 137)** and
    restarted. This was observed once with MongoDB (see Section 16).
- **Relationship with the HPA:** CPU utilisation is measured **as a percentage of the request**,
  not of the limit. Without a CPU request, the HPA cannot compute utilisation at all.
- `200m` = 0.2 of one CPU core. `250Mi` = 250 mebibytes.

Observed usage at idle during the final audit (`kubectl top pods -n flask-app`): Flask Pods
2–8m CPU and 33–54Mi memory; MongoDB 80m CPU and 202Mi memory.

## 13. Horizontal Pod Autoscaler (HPA)

### Configuration (`k8s/flask-hpa.yaml`)

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: flask-app-hpa
  namespace: flask-app
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: flask-app
  minReplicas: 2
  maxReplicas: 5
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

### How it works
- **What the HPA does:** every ~15s it reads Pod CPU usage from the **Metrics Server** and adjusts
  the Deployment's replica count to keep **average** CPU utilisation near the target.
- **Utilisation:** `utilisation = actual CPU usage ÷ CPU request`. A Pod using 140m with a 200m
  request is at 70%.
- **The target:** `70% × 200m = 140m` average per Pod. Above that, the HPA adds Pods.
- **Scale-up formula:** `desired = ceil(current replicas × current utilisation ÷ target)`.
  Example from the test: `ceil(3 × 193 / 70) = 9`, capped at `maxReplicas` = **5**.
- **Scale-down:** when usage drops, the HPA waits for a **5-minute stabilisation window**
  (default) and uses the highest recommendation seen in that window. This prevents "flapping".
  After that it scales down, never below `minReplicas`.
- **Why min/max matter:** `minReplicas: 2` keeps two Pods running even at zero load, so the app
  stays available if one Pod fails. `maxReplicas: 5` caps resource usage, so a traffic spike (or
  bug) cannot consume the whole cluster.

### Inspecting the HPA
```powershell
kubectl get hpa -n flask-app
kubectl describe hpa flask-app-hpa -n flask-app
kubectl top pods -n flask-app
kubectl get hpa -n flask-app -w     # watch live
```

Live state during the final audit:
```
NAME            REFERENCE              TARGETS       MINPODS   MAXPODS   REPLICAS
flask-app-hpa   Deployment/flask-app   cpu: 2%/70%   2         5         2
```
`ScalingActive=True (ValidMetricFound)`, so metrics are available.

### Autoscaling test: method
> The results below are **observed results from the earlier HPA test run**, recorded from live
> `kubectl` output. The load test was not repeated in the final audit.

- **Load generator:** a temporary Pod (`hpa-load-generator`) using the already-loaded
  `flask-mongodb-app:1.0` image (it contains Python, so no image pull was needed).
- **Traffic:** 3 processes × 8 threads sending continuous `GET /` requests to
  `http://flask-service:5000/` for **300 seconds**. `GET /` does not touch MongoDB, so **no data
  was written**.
- **Why HTTP traffic was enough:** each Flask Pod is a single Python process. Under concurrent
  requests its CPU rose well above the 140m target (up to ~390m, close to the 500m limit), so no
  separate CPU-burner was needed.
- **Safety controls:** the generator was capped at 1500m CPU / 200Mi memory (the node has 12
  CPUs), had a fixed worker count, `restartPolicy: Never` and `activeDeadlineSeconds: 360`. It
  was deleted after the test.
- **Monitoring:** HPA CPU, replica count and per-Pod CPU were logged every 15s. The Deployment
  was **never scaled manually**.

### Autoscaling test: observed results (2 → 3 → 5 → 2)

| Elapsed | HPA CPU | Replicas | Event |
|---|---|---|---|
| 0s | 1% | 2 | Load starts |
| ~40s | **79%** | 2 → **3** | `SuccessfulRescale: New size: 3; reason: cpu resource utilization (percentage of request) above target` |
| ~100s | **193%** | 3 → **5** | `SuccessfulRescale: New size: 5` (capped at max) |
| ~4–5 min | 104–105% | 5 | Load spread over 5 Pods (~205–219m each) |
| 300s | | 5 | Load ends |
| ~6.5 min after load ended | **1%** | 5 → **2** | `SuccessfulRescale: New size: 2; reason: All metrics below target` |

| Metric | Observed value |
|---|---|
| Initial replicas | 2 |
| CPU target | 70% (140m per Pod) |
| Peak CPU utilisation | **193%** |
| Maximum replicas | **5** (= `maxReplicas`) |
| Load test duration | 300 seconds |
| Requests generated | **101,110** |
| Approx. throughput | **~337 requests/second** |
| Final replicas after scale-down | **2** |
| Final CPU utilisation | ~1% |

CPU fell to idle within about 1.5 minutes of the load ending. The scale-down followed about
5 minutes later, which matches the default 300-second stabilisation window.

## 14. API Test Results

Tests run against `flask-service` (via `kubectl port-forward`) during the final audit.

| # | Test | Request | Status | Response |
|---|---|---|---|---|
| 1 | Welcome | `GET /` | **200** | `Welcome to the Flask app! The current time is: 2026-11-11 20:46:28` |
| 2 | Insert | `POST /data` (persistence test record, Section 15) | **201** | `{"status":"Data inserted"}` |
| 3 | Read | `GET /data` | **200** | Array of all documents, no `_id` |
| 4 | Empty JSON | `POST /data` with `{}` | **400** | `{"error":"JSON object must not be empty"}` |
| 5 | Invalid JSON | `POST /data` with `{"name": "broken"` | **400** | `{"error":"Request body must be valid JSON"}` |
| 6 | Non-object JSON | `POST /data` with `[1, 2, 3]` | **400** | `{"error":"JSON body must be an object, e.g. {\"name\": \"value\"}"}` |
| + | Unknown route | `GET /missing` | **404** | `{"error":"Endpoint not found"}` (earlier audit run) |
| + | Wrong method | `PUT /data` | **405** | `{"error":"Method not allowed"}` (earlier audit run) |

`GET /` and `GET /data` also returned **200** through NodePort 30080 from inside the Minikube
node. Both Flask Pods are registered as endpoints of `flask-service`:

```powershell
kubectl get endpointslices -n flask-app -l kubernetes.io/service-name=flask-service
```

## 15. Persistent Storage Test

**Goal:** prove that MongoDB data survives deletion and recreation of the MongoDB Pod, because
the data lives on a PersistentVolume.

### Final explicit test

**Test record** (inserted through the Flask API):
```json
{
  "name": "MongoDB Persistence Verification",
  "message": "This record must survive MongoDB Pod recreation",
  "test_id": "mongodb-persistence-final-2026",
  "source": "kubernetes-final-assignment"
}
```

| Step | Action | Observed result |
|---|---|---|
| 1 | Record PVC/PV **before** | PVC `mongodb-data-mongodb-0` **Bound**, 2Gi, StorageClass `standard`, PV `pvc-f192e6d3-2749-4797-8f2a-7e78c75ce25b` **Bound** (PV UID `bda48e17-…`) |
| 2 | Check for an existing record, then `POST /data` | No previous record with this `test_id`. Insert returned **201** `{"status":"Data inserted"}` |
| 3 | `GET /data` before deletion | **200**. The record was present (1 match), with all 4 fields identical to what was inserted. 4 documents in total |
| 4 | `kubectl delete pod mongodb-0 -n flask-app` (the **only** deletion) | Pod deleted at 20:44:04 UTC |
| 5 | Wait for StatefulSet recreation | New `mongodb-0` **Ready at 20:44:17 UTC (~13s)**. New Pod UID (`540a25c2-…` → `243e4a02-…`), new IP (`10.244.0.12` → `10.244.0.13`), **0 restarts** |
| 6 | PVC/PV **after** | PVC **Bound** to the **same** PV `pvc-f192e6d3-…`; same PVC UID and same PV UID `bda48e17-…`; PV **Bound** |
| 7 | Mount and init check | `/data/db` mounted read-write from `mongodb-data` (claim `mongodb-data-mongodb-0`). Init script did **not** re-run (data directory not empty) |
| 8 | `GET /data` after recreation | **200**. The `mongodb-persistence-final-2026` record was present and **identical**. All 4 documents were unchanged |

**Verdict: PASSED.** The record inserted before the Pod was deleted was retrieved, unchanged,
after the replacement Pod became Ready.

**Application behaviour during recreation** (Flask polled every second for 60s):
- `GET /` returned 200 every time.
- `GET /data` returned **one** `503` (at 20:44:07, while MongoDB was down), then 200 again.
- The Flask Pods had **0 restarts**. They reconnected through Service `mongodb`, whose
  ClusterIP stayed `10.110.199.51`.

### Earlier supporting evidence
- An earlier audit ran the same test with record `persistence-20261112-013458` and it also
  passed (Pod IP `10.244.0.4` → `10.244.0.11`, same PVC/PV).
- The data also survived two unplanned container restarts (one `OOMKilled`, one liveness
  restart; see Section 16).
- It survived the StatefulSet rollout that changed the probes.

### Repeat the test
```powershell
kubectl delete pod mongodb-0 -n flask-app      # deletes only the Pod; PVC and data are kept
kubectl wait --for=condition=Ready pod/mongodb-0 -n flask-app --timeout=180s
kubectl get pvc -n flask-app
kubectl get pv
```

## 16. MongoDB Probe Optimisation

### The incident
Originally, all three MongoDB probes ran `mongosh`. Each `mongosh` run starts a **Node.js**
process inside the MongoDB container (roughly 50–100Mi each), and that memory counts against the
container's 500Mi limit. `mongod` itself uses about 200–260Mi.

During the final audit, extra `mongosh` commands were run inside the MongoDB container while the
probes were also running. Together they pushed the container over **500Mi**, and it was
**`OOMKilled`** (exit code 137, restart 1). While it recovered under the 500m CPU limit, the
`mongosh` probes timed out, and the liveness probe restarted it once more (restart 2, clean
shutdown). It then stabilised by itself. All data was intact, and memory had briefly peaked at
**480Mi of 500Mi**.

### The change
- **Startup and liveness** probes were switched to **`tcpSocket` on port 27017**. The kubelet
  performs the TCP check from outside the container, so it starts **no process** inside it. A TCP
  check still fails during initialisation, because the kubelet connects to the Pod IP and the
  temporary init server listens on `127.0.0.1` only.
- **Readiness** keeps the **authenticated `mongosh`** check. It must prove that MongoDB responds,
  that the application user can authenticate, and that `flask_db` is usable, because readiness
  controls whether the `mongodb` Service sends traffic to the Pod.
- Result: at most **one** `mongosh` process runs at a time, instead of up to three.
- Resources were **not** changed (200m/250Mi requests, 500m/500Mi limits). `kubectl diff`
  confirmed that only the two probe handlers changed.
- The rollout (`kubectl rollout status statefulset/mongodb`) completed in ~16s, with 0 restarts,
  the same PVC/PV and all data intact.

### Before and after (idle, `kubectl top`)

| Measurement | Before (all-`mongosh` probes) | After (TCP startup/liveness), 18 samples over ~6 min |
|---|---|---|
| Memory | 166–264Mi idle; **480Mi** peak during the incident | **199–204Mi** (avg 203Mi) |
| CPU | ~150–200m | **63–79m** |
| Restarts during observation | 2 (incident) | **0** |

The final audit, after the change, measured MongoDB at 80m CPU and 202Mi memory.

### Limitations
- This change **reduces probe overhead**. It does **not** eliminate all memory risk.
- `mongod` itself still uses ~200Mi or more, and that grows with data and cache use (the
  WiredTiger cache is sized at 256M).
- `kubectl top` samples periodically and can miss short spikes.
- The "after" figures come from a freshly started `mongod`, so they are not a perfect
  like-for-like comparison.
- Running extra `mongosh` sessions inside the MongoDB container can still push it towards the
  limit. For ad-hoc checks, connect from a Flask Pod with PyMongo instead.
- If OOM kills happen again, the next step would be a higher memory limit. That requires changing
  the assignment's fixed values.

## 17. Design Choices and Alternatives

| Choice | Why | Alternative |
|---|---|---|
| **Flask as a Deployment** | Stateless: Pods are interchangeable, so they can be scaled and replaced freely | StatefulSet (unnecessary for stateless apps) |
| **MongoDB as a StatefulSet** | Stable Pod name (`mongodb-0`), stable DNS, and its **own** PVC that is re-attached after restarts | Deployment + single PVC (works for 1 replica, but no stable identity); managed DB (MongoDB Atlas) |
| **PersistentVolumeClaim** | Container filesystems are temporary; data must survive Pod deletion (proven in Section 15) | `emptyDir` (data lost with the Pod) |
| **MongoDB via ClusterIP** | The database only needs to be reachable by Flask, inside the cluster | NodePort/LoadBalancer (would expose the DB; not acceptable) |
| **Flask via NodePort** | Simple external access on Minikube without extra components | Ingress controller with a hostname; LoadBalancer + `minikube tunnel` |
| **Credentials in a Secret** | Kept out of images, Git and manifests; injected at runtime | External secret managers (Vault, cloud KMS), Sealed Secrets |
| **Non-sensitive config in a ConfigMap** | Database and collection names can change without rebuilding the image | Hardcoded defaults (less flexible) |
| **HPA on CPU** | CPU is available from Metrics Server out of the box and tracks request load for this app | Memory-based or custom metrics (e.g. requests/sec via Prometheus Adapter) |
| **Dedicated app user (`readWrite` on `flask_db`)** | Least privilege: a compromised app cannot manage users or touch other databases | Using root credentials in the app (insecure) |
| **TCP startup/liveness + authenticated readiness** | Liveness only needs "is mongod up?" (cheap TCP); readiness must prove the app can really log in. This avoids extra Node.js processes (Section 16) | `mongosh` for all probes (higher memory/CPU; contributed to an OOM incident) |
| **`MONGODB_URI` built at startup** | Reuses the existing Secret keys; no second copy of the password | Store a full URI as its own Secret key |
| **uv instead of pip** | Faster, reproducible installs; the official image makes it a one-line copy | pip |

## 18. Security Review

| Check | Result (verified) |
|---|---|
| No credentials in source code or YAML | ✔ Only `secretKeyRef` references (2 in the Flask Deployment, 4 in the StatefulSet) and `${VAR}` placeholders. The Secret values were not found in any project file |
| `.env` not committed | ✔ Listed in `.gitignore` (the project folder is not currently a Git repository) |
| `.env` / `.venv` excluded from the image | ✔ Listed in `.dockerignore`. The running image's `/app` contains only `app.py` and `requirements.txt` |
| Container runs as non-root | ✔ UID 1000 (`appuser`) |
| MongoDB authentication enforced | ✔ No credentials → `Unauthorized`; wrong password → `Authentication failed` |
| Flask uses the app user, not root | ✔ URI user = `MONGODB_APP_USERNAME`; root credentials are **not** present in Flask Pods |
| Least privilege | ✔ `flask_user` has only `readWrite` on `flask_db`. `listDatabases` shows only `flask_db`; reading `admin.system.users`, `createUser` and writing to another database are all `Unauthorized` |
| MongoDB not exposed externally | ✔ Only `flask-service` is NodePort; both MongoDB Services are internal |
| Credentials not logged | ✔ The app password appears in 0 Flask log lines |
| README contains no credentials | ✔ The Secret values were compared against this file; 0 occurrences |

> Kubernetes Secrets are base64-encoded, **not encrypted**, by default. Anyone with read access to
> Secrets in the namespace can decode them. Production clusters should add RBAC restrictions and
> encryption at rest or an external secret manager.

## 19. Troubleshooting

| Problem | Cause | Solution |
|---|---|---|
| **YAML probe parsing error:** `cannot unmarshal object into Go struct field ExecAction ... command of type string` | An unquoted probe command contains `: ` (e.g. `{ ping: 1 }`), so YAML parses it as a map | Wrap the whole command string in single quotes (done in this repo). Validate with `kubectl apply --dry-run=server` |
| **MongoDB init:** app user missing or `Authentication failed` | The init script runs **only on an empty `/data/db`**. If the PVC existed before the Secret was created or changed, the user was created with old credentials (or not at all) | Check `kubectl logs mongodb-0 -n flask-app \| Select-String mongodb-init`. Changing the Secret later does not change existing users |
| **MongoDB init:** `0/1 Ready` for a while on first start | Init phase. The temporary server listens on `127.0.0.1` only, so the probes intentionally fail until the real server listens on the Pod IP | Wait, and check the logs |
| **PVC provisioning:** `FailedScheduling: pod has unbound immediate PersistentVolumeClaims` | Observed briefly on the very first start while the PV was still being provisioned | Usually resolves within seconds. Check `kubectl get pvc,pv` and that `kubectl get storageclass` shows `standard (default)` |
| **NodePort from Windows:** `http://192.168.49.2:30080` not reachable | The Minikube docker driver isolates the node network | Use `minikube service flask-service -n flask-app --url` or `kubectl port-forward svc/flask-service 8080:5000 -n flask-app` |
| **HPA metrics:** `<unknown>/70%` | Metrics Server not ready, or Pods have no CPU request | `minikube addons enable metrics-server`; `kubectl top pods -n flask-app`; wait 1–2 minutes |
| **MongoDB memory pressure:** container `OOMKilled` (exit code 137) | Over the 500Mi limit. Extra `mongosh` processes inside the container are the main trigger | See Section 16. Avoid `mongosh` inside the MongoDB container; use PyMongo from a Flask Pod |
| Pod `CreateContainerConfigError` | Secret or ConfigMap (or one of its keys) missing | `kubectl describe pod <pod> -n flask-app`; `kubectl describe secret mongodb-secret -n flask-app` |
| Flask Pod `ErrImageNeverPull` / `ImagePullBackOff` | Image not loaded into Minikube | `minikube image load flask-mongodb-app:1.0`; `minikube image ls` |
| Flask returns `503 Database error` | MongoDB not Ready (e.g. while it restarts), or wrong credentials/authSource | `kubectl get pods -n flask-app`; `kubectl logs mongodb-0 -n flask-app`. A single 503 was observed during the planned Pod recreation |
| `kubectl exec ... --eval "..."` breaks in PowerShell | Windows PowerShell 5.1 mangles embedded double quotes passed to native programs | Run such commands from Git Bash, or avoid nested quotes |

**Other notes:** Flask runs on its built-in development server (`python app.py`, as specified),
which logs a "development server" warning. A production setup would use a WSGI server such as
Gunicorn.

## 20. Cleanup

Remove resources in reverse order. **Read the warnings first.**

```powershell
# Application tier (safe: no data stored here)
kubectl delete -f k8s/flask-hpa.yaml
kubectl delete -f k8s/flask-service.yaml
kubectl delete -f k8s/flask-deployment.yaml

# Database tier (deleting the StatefulSet keeps the PVC and its data)
kubectl delete -f k8s/mongodb-statefulset.yaml
kubectl delete -f k8s/mongodb-service.yaml
kubectl delete -f k8s/mongodb-init-configmap.yaml
kubectl delete -f k8s/configmap.yaml
```

> ⚠️ **Data loss warning:** the PVC is **not** deleted by the commands above. Deleting it
> **permanently deletes all MongoDB data**, because the PV's reclaim policy is `Delete`:
> ```powershell
> kubectl delete pvc mongodb-data-mongodb-0 -n flask-app   # IRREVERSIBLE: deletes MongoDB data
> ```

> ⚠️ Deleting the namespace deletes **everything** in it, including the Secret and the PVC (data):
> ```powershell
> kubectl delete namespace flask-app                        # IRREVERSIBLE
> ```

To pause instead of deleting: `minikube stop` (all resources and data are kept).

## 21. Final Assignment Checklist

**PASS** = verified against the live cluster with observed output. **NOT VERIFIED** = documented,
but not executed in this project's testing.

| # | Requirement | Status | Evidence |
|---|---|:---:|---|
| 1 | `GET /` returns welcome message + date/time | **PASS** | 200, Section 14 |
| 2 | `POST /data` inserts JSON into MongoDB | **PASS** | 201, Sections 14–15 |
| 3 | `GET /data` returns documents without `_id` | **PASS** | 200, Section 14 |
| 4 | Input validation (empty / invalid / non-object JSON) | **PASS** | 400 ×3, Section 14 |
| 5 | `MONGODB_URI` from environment; no credentials in code | **PASS** | Section 18 |
| 6 | App listens on `0.0.0.0:5000` | **PASS** | Flask log: `Running on all addresses (0.0.0.0)` |
| 7 | Docker image: Python 3.11-slim, uv, non-root, port 5000 | **PASS** | Section 8; image contents and UID checked in a running Pod |
| 8 | Image loaded into Minikube and used by the Deployment | **PASS** | Pods run `flask-mongodb-app:1.0` with `IfNotPresent` |
| 9 | Local Docker test (independent of Kubernetes) | **PASS** | `flask-mongodb-app:1.1` + temporary MongoDB: 200 / 201 / 200 / 400 ×3 (Section 8) |
| 10 | Image pushed to Docker Hub | **NOT VERIFIED** | Pending Docker Hub login and username confirmation (Section 8) |
| 11 | MongoDB StatefulSet, 1 replica, Running 1/1 | **PASS** | `kubectl get statefulsets -n flask-app` |
| 12 | MongoDB authentication enabled | **PASS** | `Unauthorized` without credentials; `Authentication failed` with a wrong password |
| 13 | Dedicated app user with `readWrite` on `flask_db` | **PASS** | `connectionStatus` → `flask_user`, `readWrite@flask_db` |
| 14 | PVC 2Gi RWO `standard` Bound; PV Bound; `/data/db` mounted | **PASS** | Sections 10, 15 |
| 15 | Data persists after MongoDB Pod recreation | **PASS** | Section 15 (`mongodb-persistence-final-2026`) |
| 16 | MongoDB ClusterIP + headless Service; not externally exposed | **PASS** | Section 11 |
| 17 | Flask Deployment with 2 Ready replicas | **PASS** | `2/2` |
| 18 | Flask NodePort Service on 30080 with both Pods as endpoints | **PASS** | Sections 11, 14 |
| 19 | Flask reaches MongoDB via Kubernetes DNS (`mongodb`) | **PASS** | Section 11 DNS test |
| 20 | Secret for credentials, ConfigMap for configuration | **PASS** | Sections 9, 18 |
| 21 | Requests 200m/250Mi, limits 500m/500Mi on both workloads | **PASS** | Section 12 |
| 22 | HPA: `flask-app`, min 2, max 5, CPU 70%, metrics available | **PASS** | Section 13 |
| 23 | Autoscaling observed (2 → 3 → 5 → 2) | **PASS** | Section 13 (observed in the earlier HPA test run) |
| 24 | MongoDB probe optimisation without changing resource limits | **PASS** | Section 16 |

**Known residual risk:** MongoDB's memory headroom under the fixed 500Mi limit is reduced by the
probe optimisation, but it is not eliminated (Section 16).
