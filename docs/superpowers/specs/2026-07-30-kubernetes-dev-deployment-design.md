# Kubernetes Dev Deployment Design

## Goal

Package the middle-platform Agent as a reproducible container and deploy it to the company dev Kubernetes environment through the same GitLab CI, private registry, Kustomize GitOps repository, and ArgoCD workflow used by the Java middle-platform service.

The deployment must run without Telepresence, use the dev PostgreSQL and pgvector services directly, preserve the current web, Feishu, source-sync, Bug Graph, memory, and quality capabilities, and never place credentials in the image or repository.

## Chosen Delivery Model

Use the existing company GitOps flow:

1. GitLab Runner executes tests and builds the image with `buildah` or `nerdctl`.
2. The image is pushed to the company registry as `middle-platform-agent-rag:<timestamp>-<branch>`.
3. CI updates the image in the Kubernetes YAML repository at `api-center/middle-platform-agent-rag/env/oci-develop`.
4. ArgoCD synchronizes the `oci-middle-platform-agent-rag-develop` application and waits for rollout health.

Direct `kubectl apply` and a new Helm chart are excluded from this change.

## Container Image

Use a multi-stage Docker build:

- A Node 22 Bookworm stage runs `npm ci`, frontend tests when requested by CI, and `npm run build`.
- A second Node stage installs production-only frontend dependencies required by the runtime Vue SFC parser.
- A Python 3.11.9 Bookworm stage builds Python wheels.
- The runtime is based on Python 3.11.9 slim Bookworm and contains only the application, installed wheels, built frontend, Node runtime, production Vue parser dependencies, Git, CA certificates, and timezone data.

The runtime must:

- run as a non-root user;
- expose port 8000;
- start exactly one Uvicorn worker through `python -m knowledge.run_api`;
- set `TZ=Asia/Shanghai`, `PYTHONUNBUFFERED=1`, and a writable `HOME` below `/app/storage`;
- include an OCI health check against `/health/live`;
- exclude `.env`, `.git`, local storage, logs, caches, tests, IDE files, and development dependencies through `.dockerignore`.

The image does not contain any model key, database credential, GitLab token, Grafana token, Feishu secret, admin password, source document, embedding, or runtime log.

## Kubernetes Workload

Create Kustomize-ready base resources for:

- `Deployment/middle-platform-agent-rag`
- `Service/middle-platform-agent-rag` on port 8000
- `PersistentVolumeClaim/middle-platform-agent-rag-storage`
- non-sensitive `ConfigMap/middle-platform-agent-rag-config`

Deployment decisions:

- `replicas: 1`
- `strategy.type: Recreate`
- container port 8000
- resource requests: 1 CPU and 2 GiB memory
- resource limits: 2 CPU and 4 GiB memory
- PVC: 20 GiB, `ReadWriteOnce`, mounted at `/app/storage`
- pod security context supplies an `fsGroup` compatible with the non-root application user
- termination grace period is 90 seconds so in-flight requests and background workers can close cleanly

`Recreate` is required because one process currently owns the Feishu long connection, source-sync worker, evaluation worker, memory worker, local Git mirror coordination, and in-process retrieval caches. A rolling deployment could briefly run two active bots and two worker sets.

## Probes and Lifecycle

- Startup probe: `/health/live`, tolerant of a five-minute cold start.
- Liveness probe: `/health/live`; it only checks that the API process is alive.
- Readiness probe: `/health/ready`; the pod receives traffic only after required components initialize.
- A `preStop` delay gives the service endpoint time to drain before process termination.

Readiness remains dependency-aware. Optional integrations may be reported as `disabled`, while required PostgreSQL and pgvector components must be `available` before dev acceptance.

## Configuration and Secrets

The dev ConfigMap contains only non-sensitive settings, including:

```dotenv
DATA_STORE_PROVIDER=postgres
VECTOR_STORE_PROVIDER=pgvector
VECTOR_SHADOW_ENABLED=false
BUG_GRAPH_CHECKPOINT_PROVIDER=postgres
DATABASE_SSL_MODE=require
DATABASE_POOL_SIZE=5
DATABASE_MAX_OVERFLOW=5
DATABASE_POOL_TIMEOUT_SECONDS=10
DATABASE_POOL_RECYCLE_SECONDS=1800
DATABASE_STATEMENT_TIMEOUT_SECONDS=30
KNOWLEDGE_STORAGE_ROOT=/app/storage
FRONTEND_DIST=/app/web/dist
LOG_LEVEL=INFO
SOURCE_WORKER_ENABLED=true
FEISHU_BOT_ENABLED=true
```

The Deployment loads sensitive values from a pre-created Kubernetes Secret named `middle-platform-agent-rag-secrets`. The repository contains only a documented key list, never a Secret manifest with values. The key list covers the database DSN or split PostgreSQL credentials, model and embedding keys, GitLab read token, Grafana token, Feishu application credentials, catalog encryption key, admin password hash, and any MCP bearer token.

The local Telepresence overrides are not copied into the image. In particular, dev uses the normal connection pool and PostgreSQL Bug Graph checkpointer rather than the local SQLite checkpoint workaround.

## Persistent and Ephemeral Data

PostgreSQL stores relational state, LangGraph checkpoints, quality data, auth data, memory data, and source catalogs. pgvector stores knowledge and memory vectors.

The PVC stores only data that still requires a filesystem:

- Git mirrors and working copies;
- uploaded source documents and cached Swagger artifacts;
- temporary import state that must survive a pod restart;
- optional rotating application logs if file logging remains enabled.

Container logs are emitted to stdout/stderr for Kubernetes collection. Logging configuration will support disabling the rotating file handler in Kubernetes so the normal dev mode does not duplicate logs on the PVC.

## Network and Access

The application Service is initially `ClusterIP`. The internal hostname or gateway route is owned by the existing Kubernetes YAML repository and is not hardcoded in this repository. Feishu message reception continues through the outbound long connection and does not require a public callback endpoint.

The pod requires outbound access to PostgreSQL, the model relay, the embedding service, GitLab, Grafana, Feishu, the metric MCP service, and configured Swagger endpoints. NetworkPolicy changes, DNS records, and gateway certificates are infrastructure prerequisites outside this repository.

## CI/CD

Add a Python-specific `.gitlab-ci.yml` and shell scripts following the Java service conventions while keeping credentials in protected GitLab CI variables.

The pipeline stages are:

1. `test`: backend pytest, frontend Vitest, and frontend build.
2. `build`: build and push an immutable image; also publish a cache image when the runner supports it.
3. `deploy-dev`: clone the Kubernetes YAML repository, update the Kustomize image, commit and push the change, then synchronize and wait for ArgoCD.

The deploy job is serialized with a GitLab `resource_group`, runs only for the agreed dev branch or manual invocation, and never prints registry, GitOps, ArgoCD, database, or application credentials.

## Failure Handling and Rollback

- A failed test prevents image creation.
- A failed image push prevents GitOps changes.
- A failed GitOps commit or ArgoCD health wait fails the pipeline.
- Kubernetes keeps the previous ReplicaSet available until `Recreate` begins; rollback is performed by reverting the GitOps image commit and synchronizing ArgoCD.
- Database migrations run as a separate pre-deployment job or explicit operator step, not automatically in every pod startup.
- The first dev release remains one replica. Multi-replica support requires separating Feishu and worker ownership before changing this constraint.

## Verification

Before deployment:

- build the container locally or in CI;
- run an image smoke test as the non-root user;
- confirm `.env`, runtime logs, storage data, and credentials are absent from image layers;
- validate Kustomize output and Kubernetes schemas;
- run backend tests, frontend tests, and frontend build.

After ArgoCD reports healthy:

- verify `/health/live` and `/health/ready`;
- confirm PostgreSQL, pgvector, Bug Graph, catalog, workers, Grafana, Feishu, and MCP readiness states;
- verify only one pod and one Feishu connection are active;
- run approval-flow, metric-platform, workflow, Bug trace, history, citation detail, and memory smoke cases;
- confirm Git source sync can write the PVC and PostgreSQL/pgvector counts remain consistent.

## External Prerequisites

Operations must provide, before the first deployment:

- the private Python and Node base-image path if public base images are unavailable to the runner;
- the GitOps directory and ArgoCD application named above, or equivalent names updated consistently in CI variables;
- the `middle-platform-agent-rag-secrets` Secret;
- a 20 GiB storage class-backed PVC;
- service-network access to all required internal and external dependencies;
- the internal gateway route used by employees to open the web application.

