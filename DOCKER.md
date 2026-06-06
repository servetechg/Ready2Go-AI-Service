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

- **Ports**: `"8000:8000"` maps `localhost:8000` (your laptop) → port 8000
  inside the `api` container. Same idea for Weaviate's `8080` and `50051`.
- **Service names as hostnames**: containers reach each other by service name on
  Compose's private network. That's why `WEAVIATE_URL=http://weaviate:8080`
  (not `localhost` — inside a container `localhost` means that container itself).
- **Config**: `env_file: .env` loads all vars (incl. Atlas `MONGODB_URI`); the
  `environment:` block overrides only container-specific ones (`WEAVIATE_URL`,
  `RELOAD`).

---

## Troubleshooting

| Symptom                                   | Cause / fix                                                        |
| ----------------------------------------- | ----------------------------------------------------------------- |
| Build fails: `Readme file does not exist` | `README.md` excluded by `.dockerignore` — keep the `!README.md` line |
| `/readyz` shows weaviate "degraded"       | Weaviate container not up yet — `docker compose ps`, wait, retry   |
| `/readyz` shows mongodb "degraded"        | Atlas unreachable / bad `MONGODB_URI` in `.env`                    |
| Port 8000 already in use                  | A local `uv run main` is running — stop it (don't run both)        |
| Startup seems to hang ~15s                | Normal — Atlas handshake. Wait for `Application startup complete`. |
| Analyze returns `502`                     | `fileUrl` not reachable — use a real document URL                 |
| Analyze returns `401`                     | Missing/wrong `Authorization: Bearer <PYTHON_INTEGRITY_TOKEN>`    |
