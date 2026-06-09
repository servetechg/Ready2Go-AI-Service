# Running the Ready2Go AI Service with Docker

A practical, copy-paste guide for running the service every time. Run all
commands from the project root: `/home/mehmood/Ready2Go-AI-Service`.

---

## TL;DR — start it

```bash
docker compose up -d --build     # build + start API and Weaviate
docker compose logs -f api       # watch startup (Ctrl+C to stop watching)
curl http://localhost:8000/readyz
```

Stop it:

```bash
docker compose down              # stop containers, KEEP Weaviate data
```

---

## The mental model

```
Dockerfile  --(docker compose build)-->  Image  --(docker compose up)-->  Container
```

- **Image** = a frozen snapshot of the app + Python + dependencies (built once).
- **Container** = a running copy of an image (start/stop/delete freely).
- **Compose** = runs multiple containers together from `docker-compose.yml`.

This stack has **two containers**:

| Container  | What it is                  | Where its data lives                          |
| ---------- | --------------------------- | --------------------------------------------- |
| `api`      | the Python FastAPI service  | stateless (writes go to Atlas + Weaviate)     |
| `weaviate` | local vector database       | Docker volume `weaviate_data` (persists)      |

**MongoDB is NOT a container** — the service connects to the **Atlas** cluster
defined by `MONGODB_URI` in `.env`. Python keeps its own `ai_*` collections in
that same `ready2go` DB.

---

## Do I start the Python service separately?

**No.** There are two mutually exclusive ways to run — pick ONE:

| Way       | Command                  | Python runs...                          |
| --------- | ------------------------ | --------------------------------------- |
| Local dev | `uv run main`            | directly on your laptop                 |
| Docker    | `docker compose up -d`   | inside the `api` container (automatic)  |

With Docker you do **not** run `uv run main`. The Dockerfile's `CMD ["main"]`
boots the service automatically when the container starts. Don't run both at
once — they'd collide on port 8000.

---

## Where is the Weaviate data, and is it safe?

Stored in the Docker named volume `weaviate_data` (mounted at
`/var/lib/weaviate` inside the container).

| Action                          | Weaviate data        |
| ------------------------------- | -------------------- |
| `docker compose down`           | ✅ survives          |
| `docker compose restart` / reboot | ✅ survives        |
| `docker compose up` again       | ✅ reused            |
| `docker compose down -v`        | ❌ **deleted** (`-v` wipes volumes) |

Only `down -v` erases vectors. Mongo `ai_*` data is on Atlas and is never
touched by Docker.

### Storing the vector data somewhere else (another drive / NFS / cloud mount)

Set **`WEAVIATE_DATA_PATH`** in `.env` to an absolute host path. Compose then bind‑mounts
that directory instead of the named volume — so the entire vector store (vectors + text +
metadata) lives wherever you point it:

```bash
WEAVIATE_DATA_PATH=/mnt/bigdisk/r2g/weaviate   # in .env, then:
docker compose up -d
```

Leave it unset to keep the default named `weaviate_data` volume. For a fully managed/cloud
Weaviate, don't run the local `weaviate` service at all — set `WEAVIATE_URL` (and
`WEAVIATE_API_KEY`, `WEAVIATE_GRPC_PORT`) to the managed endpoint.

### Turning file logs off / relocating them

- `LOG_TO_FILE=false` → the service logs to the console only (nothing written to disk).
- `LOG_DIR=/mnt/bigdisk/r2g/logs` (or set `DATA_DIR` to move logs **and** the default
  Weaviate path together) → relocate the `app.log` / `error.log` files.

---

## Step-by-step

### 1. Build the image
```bash
docker compose build api
```
Re-run whenever you change code or dependencies.

### 2. Start the stack
```bash
docker compose up -d        # -d = background; omit to watch logs in foreground
```

### 3. Check it's running
```bash
docker compose ps           # want: api + weaviate "running" (api becomes "healthy")
```

### 4. Watch startup logs
```bash
docker compose logs -f api
```
Look for: `service.startup` → `startup.weaviate_collections_ready` →
`startup.mongo_indexes_ready` → `Application startup complete`.
⚠️ The Atlas handshake takes ~14s, so startup is not instant.

### 5. Test the endpoints
```bash
curl http://localhost:8000/healthz      # liveness
curl http://localhost:8000/readyz       # all three deps should say "ok"
# Browser: http://localhost:8000/docs   (interactive API docs)
```

### 6. Run a real analyze call
The request body is **nested** (`tenantContext`, `plan`, `attachment` objects) —
this matches the Pydantic contract in `app/schemas.py`. A flat body returns `422`.

```bash
curl -X POST http://localhost:8000/v1/integrity/analyze \
  -H "Authorization: Bearer change-me-long-random-token" \
  -H "Content-Type: application/json" \
  -d '{
    "tenantContext": {
      "tenantKey": "tenant-demo",
      "actorUserId": "6a054db0dc2d5c9cc796a36b"
    },
    "plan": {
      "planId": "plan-abc",
      "label": "Main BCP",
      "category": "bcp",
      "overview": "",
      "steps": []
    },
    "attachment": {
      "attachmentId": "test-001",
      "fileName": "BCP_Procedures.pdf",
      "fileExtension": "pdf",
      "fileUrl": "https://your-real-cloudinary-url/sample.pdf"
    }
  }'
```
- The token comes from `PYTHON_INTEGRITY_TOKEN` in `.env`.
- `fileExtension` must be one of `pdf | docx | csv | xlsx`.
- `fileUrl` must be a **real, reachable** document — the pipeline downloads and
  parses it. A fake URL returns `502` (fetch failed), which is correct behavior.
- A `degraded: true` in the response `details` means a dependency
  (Weaviate/OpenAI) was unreachable; that result is **not cached** and will be
  re-analyzed on the next call.

### 7. Stop when done
```bash
docker compose down         # keep Weaviate data
docker compose down -v      # ALSO wipe Weaviate data (fresh start)
```

---

## Command cheat-sheet

| Goal                       | Command                           |
| -------------------------- | --------------------------------- |
| Build / rebuild image      | `docker compose build api`        |
| Start everything           | `docker compose up -d`            |
| Rebuild + restart in one   | `docker compose up -d --build`    |
| Stop everything            | `docker compose down`             |
| Stop + wipe Weaviate data  | `docker compose down -v`          |
| See what's running         | `docker compose ps`               |
| Live logs                  | `docker compose logs -f api`      |
| Restart just the api       | `docker compose restart api`      |
| Shell inside the container | `docker compose exec api bash`    |

---

## How the pieces connect

- **Everything is env-driven**: `docker-compose.yml` uses `${VAR:-default}`
  interpolation — `API_PORT`, `WEAVIATE_IMAGE`, `WEAVIATE_HTTP_PORT`,
  `WEAVIATE_GRPC_PORT`, `WEAVIATE_QUERY_DEFAULTS_LIMIT`, `WEAVIATE_DATA_PATH`,
  `WEAVIATE_INTERNAL_URL`, `RELOAD`. Nothing infra-relevant is hardcoded; set any
  of these in `.env` to change it.
- **Ports**: `"${API_PORT:-8000}:${PORT:-8000}"` maps host `API_PORT` → the app's
  `PORT` inside the `api` container. Same idea for Weaviate's HTTP/gRPC ports.
- **Service names as hostnames**: containers reach each other by service name on
  Compose's private network. That's why the API's in-network URL is
  `WEAVIATE_INTERNAL_URL` (default `http://weaviate:8080`) — not `localhost`,
  which inside a container means that container itself.
- **Config**: `env_file: .env` loads all vars (incl. Atlas `MONGODB_URI`); the
  `environment:` block sets the container-specific in-network values
  (`WEAVIATE_URL` from `WEAVIATE_INTERNAL_URL`, `RELOAD`, `LOG_DIR=/app/logs`).
- **Runs as you**: the `api` service sets `user: ${HOST_UID:-1000}:${HOST_GID:-1000}`
  so files written to bind mounts (`./logs`) are owned by your host user, not the
  image's UID-999 `app` user. See the UID gotcha below if `id` shows non-1000.
- **Persistence on the host**: `./logs` → `/app/logs` and `WEAVIATE_DATA_PATH` →
  `/var/lib/weaviate` are bind-mounted, so logs and vectors live on your disk and
  survive container removal. Weaviate pins `CLUSTER_HOSTNAME=node1` so its RAFT
  state stays valid across `compose up`/`down` (see the gotcha below).

---

## First-run gotchas (and why they happen)

These bit us on the first dockerized run. All are already fixed in the current
`docker-compose.yml` / host config — this section explains the reasoning so you can
fix them again on a fresh machine.

### Build can't pull images — `dial tcp: lookup ghcr.io on 127.0.0.53:53: i/o timeout`

The Docker daemon resolves image names (`docker/dockerfile:1`, `python:3.12-slim`,
`ghcr.io/astral-sh/uv`) via the **host's** `/etc/resolv.conf`, which on Ubuntu points
at the systemd-resolved stub `127.0.0.53` → your router. If the router's DNS is flaky,
pulls time out intermittently (works once, fails the next).

> ⚠️ Setting `"dns"` in `/etc/docker/daemon.json` does **not** fix this — that only
> gives DNS to *running containers*, not to the daemon's own image pulls.

Fix the **host resolver** so the stub has a reliable upstream:

```bash
sudo mkdir -p /etc/systemd/resolved.conf.d
sudo bash -c 'printf "[Resolve]\nDNS=8.8.8.8 8.8.4.4\nFallbackDNS=1.1.1.1\n" > /etc/systemd/resolved.conf.d/dns.conf'
sudo systemctl restart systemd-resolved
resolvectl status | grep "Current DNS"   # should now show 8.8.8.8
```

### `PermissionError: '/app/logs/app.log'` — UID mismatch on the log bind mount

The image's `app` user is **UID 999**; your host user is **UID 1000**. With `./logs`
bind-mounted, files written by one can't be written by the other. The compose `api`
service therefore runs as the **host user**:

```yaml
user: "${HOST_UID:-1000}:${HOST_GID:-1000}"
```

The default `1000:1000` is the typical first Linux user. If `id` shows different
numbers, either set `HOST_UID`/`HOST_GID` in `.env` or run:

```bash
HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose up -d
```

This keeps log files owned by you, so `docker compose` and local `uv run main` can
share the same `./logs` folder without clashing.

### Weaviate: `403 ... leader not found`, logs loop on "attempting to join"

Weaviate 1.25+ runs an internal RAFT consensus layer. By default it keys its node
identity to the **container's IP** (`172.18.0.x`), which changes on every
`compose up`. With a persistent data path, the saved raft state then references an
IP that no longer exists → the node never elects a leader → every schema call 403s.

Fixed by pinning a **stable node identity** in the `weaviate` service:

```yaml
CLUSTER_HOSTNAME: "node1"
RAFT_BOOTSTRAP_EXPECT: "1"
```

If you hit this **after** raft state was already written with the old (IP-keyed)
identity, you must clear it once. The `raft` dir is created by root, so wipe it via a
root container (no host `sudo` needed):

```bash
docker compose down
docker run --rm -v "${WEAVIATE_DATA_PATH:-/home/mehmood/data/weaviate}":/data alpine \
  sh -c 'rm -rf /data/* /data/.[!.]*'
docker compose up -d        # watch for: "raft election won" → schema returns 200
```

> This wipes vectors too — only safe on a fresh/empty store. Once `CLUSTER_HOSTNAME`
> is set, normal `up`/`down` cycles keep the data and the error won't recur.

### Bind-mount directories must exist and be writable

Create the host dirs and make them writable before first run (the Weaviate container
writes as root; the api container as your UID):

```bash
mkdir -p /home/mehmood/data/weaviate ./logs
chmod 777 /home/mehmood/data/weaviate ./logs
```

---

## Troubleshooting

| Symptom                                   | Cause / fix                                                        |
| ----------------------------------------- | ----------------------------------------------------------------- |
| Build fails: `Readme file does not exist` | `README.md` excluded by `.dockerignore` — keep the `!README.md` line |
| Build fails: `lookup ... on 127.0.0.53: i/o timeout` | Host DNS flaky — see "Build can't pull images" above   |
| `PermissionError: '/app/logs/app.log'`    | UID mismatch — set `HOST_UID`/`HOST_GID` (see gotchas above)       |
| Weaviate `403 leader not found` / join loop | Stale RAFT state — clear data dir, keep `CLUSTER_HOSTNAME` (above) |
| `/readyz` shows weaviate "degraded"       | Weaviate container not up yet — `docker compose ps`, wait, retry   |
| `/readyz` shows mongodb "degraded"        | Atlas unreachable / bad `MONGODB_URI` in `.env`                    |
| Port 8000 already in use                  | A local `uv run main` is running — stop it (don't run both)        |
| Startup seems to hang ~15s                | Normal — Atlas handshake. Wait for `Application startup complete`. |
| Analyze returns `502`                     | `fileUrl` not reachable — use a real document URL                 |
| Analyze returns `401`                     | Missing/wrong `Authorization: Bearer <PYTHON_INTEGRITY_TOKEN>`    |
