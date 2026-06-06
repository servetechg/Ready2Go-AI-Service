# Weaviate — Codebase & Architecture Context File

> Purpose: a verified, agent-ready reference for working with the **Weaviate** open-source vector database — architecture, configuration, modules, and a file-level map of the repo. Built by cloning and inspecting the live source tree (`github.com/weaviate/weaviate`, `main`) plus the official README and the maintainers' own `CLAUDE.md`. This is intended to be loaded as context for coding agents that read, query, configure, or extend Weaviate.

---

## 0. Provenance & versions (VERIFIED)
- **Source inspected:** `git clone --depth 1 https://github.com/weaviate/weaviate` on `main`, commit `82695961…` (committed 2026-06-03).
- **Latest tagged release at research time:** **v1.36.2** ("Vector index zero-length vector Fix, Quantization with cache issue Fix", Feb 27, 2026). 502 total releases. The quickstart in the README pins `weaviate:1.36.0`.
- **License:** **BSD-3-Clause**.
- **Language mix:** Go **96.8%**, Assembly 1.2% (SIMD distance kernels), Python 1.0% (test/CI helpers), Shell, C.
- **Module path:** `github.com/weaviate/weaviate`.
- **Repo size signals:** ~24k commits, ~15.7k stars, ~1.2k forks.
- Everything below is read directly from source unless explicitly flagged otherwise.

---

## 1. What Weaviate is

Weaviate is an **open-source, cloud-native vector database written in Go** that stores both **objects and their vectors**, enabling semantic search at scale. It combines, in a single query interface: vector similarity (ANN) search, keyword/**BM25** search, **hybrid** search, structured **filtering**, **RAG (generative search)**, and **reranking**. Common use cases: RAG systems, semantic/image search, recommendation engines, chatbots, classification.

Two ways to get vectors in:
1. **Automatic vectorization at import** via integrated model-provider modules (OpenAI, Cohere, HuggingFace, Google, Anthropic, Ollama, etc.).
2. **Bring-your-own pre-computed vectors** (`self_provided`).

Production features: built-in **multi-tenancy**, **replication**, **RBAC**, **vector compression/quantization**, **object TTL**, horizontal scaling.

---

## 2. APIs and client libraries (VERIFIED)

Weaviate exposes three server APIs:
- **REST** — generated from OpenAPI specs (`openapi-specs/`) via go-swagger. CRUD, schema, backup, nodes, classification, etc.
- **gRPC** — high-throughput path for search, batch, aggregation (proto in `grpc/proto/v1`, also `v0`). Default port **50051**.
- **GraphQL** — query layer for `Get`/`Aggregate`/`Explore`.

Official clients: **Python** (`pip install -U weaviate-client`), **JavaScript/TypeScript**, **Java**, **Go**, **C#/.NET**, plus community libraries.

Minimal Python example (from README, VERIFIED):
```python
import weaviate
from weaviate.classes.config import Configure, DataType, Property

client = weaviate.connect_to_local()
client.collections.create(
    name="Article",
    properties=[Property(name="content", data_type=DataType.TEXT)],
    vector_config=Configure.Vectors.text2vec_model2vec(),  # or .self_provided()
)
articles = client.collections.get("Article")
articles.data.insert_many([
    {"content": "Vector databases enable semantic search"},
    {"content": "Machine learning models generate embeddings"},
])
results = articles.query.near_text(query="Search objects by meaning", limit=1)
print(results.objects[0])
client.close()
```

---

## 3. Architecture overview — hexagonal (ports & adapters)

The maintainers describe the codebase as a **hexagonal (ports-and-adapters) architecture** (source: repo `CLAUDE.md`). Layers:

```
cmd/weaviate-server/   → Entry point (go-swagger generated main)
adapters/              → "Adapters" — translate the outside world to/from the core
  handlers/            → Inbound: API surfaces
    rest/              → REST handlers (go-swagger generated + configure_api.go wiring)
    grpc/              → gRPC handlers (search, batch, aggregation)
    graphql/           → GraphQL layer
    mcp/               → Model Context Protocol server handlers
  clients/             → Outbound HTTP clients to module inference services
  repos/               → Outbound: persistence implementations
    db/                → Database/storage engine (the heart)
    schema/            → Schema repository
    classifications/   → Classification persistence
    modules/           → Module repo wiring
    transactions/      → Distributed transaction plumbing
usecases/              → Business logic (pure-ish core; see §7)
entities/              → Domain models, interfaces, shared types (the "ports")
modules/               → Plugin modules: vectorizers, generative, rerankers, backup, usage
cluster/               → Distributed consensus (RAFT), replication, sharding, RBAC, schema
grpc/proto/            → gRPC protobuf definitions (v0, v1)
openapi-specs/         → OpenAPI source for REST codegen
```

### Startup wiring (VERIFIED)
All initialization happens in **`adapters/handlers/rest/configure_api.go` → `MakeAppState()`**: DB creation, schema manager, cluster/RAFT services, module registration, gRPC server startup, monitoring. This is the single best entry point for an agent tracing "how does the server boot."

---

## 4. The data path: DB → Index → Shard → Store (VERIFIED)

This is the core mental model for storage. Source: `adapters/repos/db/` and `CLAUDE.md`.

- **DB** (top level) — holds all **indices**, one per collection/class. (`adapters/repos/db/`)
- **Index** (per collection) — manages **shards** and multi-tenancy. (`adapters/repos/db/index.go`)
- **Shard** — the unit of storage. Contains:
  - an **LSM store** for objects/properties,
  - one or more **vector indexes**,
  - an **inverted index** for filtering/BM25.
- **LSM Store** (`adapters/repos/db/lsmkv/`) — a **custom LSM key-value store** with three bucket strategies:
  - **Replace** — classic KV, one value per key (objects).
  - **Set** — unordered multi-value per key (inverted index / filterable).
  - **Map** — key→(k,v pairs) per key (BM25 term frequencies / searchable).
  - Also: **RoaringSet** / **RoaringSetRange** buckets for compressed bitmap filtering and range queries.

### LSM internals worth knowing (`adapters/repos/db/lsmkv/`)
- `bucket.go`, `bucket_options.go` — bucket lifecycle and strategy config.
- `memtable*` + `binary_search_tree*.go` — in-memory write buffer (red-black/BST variants per strategy).
- `commitlogger*.go` + `commitlogger_parser_*.go` — **WAL** (write-ahead log) and its per-strategy parsers (`replace`, `collection`, `roaring_set`, `roaring_set_range`).
- `bucket_recover_from_wal.go` — crash recovery.
- `compactor_replace.go` / `compactor_set.go` / `compactor_map.go` / `compactor_inverted.go` — **segment compaction** per strategy.
- `cursor_*` — iteration over memtable and on-disk segments.
- `bucket_snapshot.go`, `bucket_backup.go` — snapshotting/backup hooks.

### Hot-path performance conventions (VERIFIED from `CLAUDE.md`)
- Avoid `binary.Read` (allocates heavily) — use the **`usecases/byteops`** package.
- Never use bare `go` statements — always wrap via **`entities/errors/go_wrapper.go`** (enforced by `tools/linter_go_routines.sh`).
- Logging: logrus, error text goes in the **message body** (`Warnf("...: %v", err)`), never `WithError`.

---

## 5. Vector indexes (VERIFIED — `adapters/repos/db/vector/`)

Three index types plus support packages:
- **HNSW** (`vector/hnsw/`) — primary ANN index. Supports **PQ/BQ/SQ/RQ compression**, **multi-vector**, **tombstone cleanup**, commit-log + snapshotting, ACORN filtered search.
- **Flat** (`vector/flat/`) — brute-force, best for small datasets/tenants.
- **Dynamic** (`vector/dynamic/`) — auto-switches flat → HNSW once the dataset grows past a threshold.

Support packages: `cache/` (vector cache + prefiller), `compressionhelpers/`, `kmeans/` (PQ codebook training), `multivector/` (ColBERT-style late interaction / MUVERA), `geo/` (geo-coordinate index), `common/` (shared config + distances), `hfresh/`, `noop/`, `selection/`.

### HNSW user-config defaults (VERIFIED — `entities/vectorindex/hnsw/config.go`)
| Param | Default | Notes |
|---|---|---|
| `maxConnections` | **32** | min 4, max 2047 |
| `efConstruction` | **128** | min 4 |
| `ef` | **-1** | -1 = let Weaviate pick dynamically |
| `dynamicEfMin` | **100** | used when `ef = -1` |
| `dynamicEfMax` | **500** | |
| `dynamicEfFactor` | **8** | |
| `cleanupIntervalSeconds` | **300** | tombstone cleanup cadence |
| `flatSearchCutoff` | **40000** | below this, brute-force even with HNSW |
| `filterStrategy` | **acorn** | filtered-search algorithm |
| `skip` | **false** | true = don't index this vector |
| `distance` | **cosine** | default metric (see below) |

### Distance metrics (VERIFIED — `entities/vectorindex/common/`)
Default is **`cosine`**. Others available include dot, L2-squared, manhattan, hamming (see `common/` distance constants).

### Compression / quantization (VERIFIED — `entities/vectorindex/compression/`)
Data structures exist for: **PQ** (`pq_data.go`; encoders `kmeans`/`tile`, distributions `normal`/`log-normal`), **SQ** (`sq_data.go`), **RQ** (`rq_data.go`; flat default `RQBits = 8`), **BRQ** (`brq_data.go`), **MUVERA** multi-vector encoding (`muvera_data.go`), plus `fast_rotation.go`. Flat index compression is **off by default** (`DefaultCompressionEnabled = false`).

---

## 6. Cluster, consensus, replication (VERIFIED — `cluster/`)

Weaviate uses **RAFT** for distributed metadata/schema consensus (HashiCorp raft under the hood).

- `cluster/raft.go`, `cluster/store.go`, `cluster/store_apply.go`, `cluster/service.go` — the RAFT FSM store: apply/query split, snapshotting (`store_snapshot.go`).
- **Apply/query endpoint split** (command pattern): `raft_apply_endpoints.go` / `raft_query_endpoints.go`, with domain-specific variants for `alias`, `namespace` (tenants), `rbac`, `replication`, `dynuser`, `distributed_tasks`.
- `cluster/replication/` — replica movement & replication engine.
- `cluster/rbac/` — role-based access control state machine.
- `cluster/schema/` — schema as replicated state.
- `cluster/bootstrap/`, `cluster/resolver/`, `cluster/router/`, `cluster/rpc/` — cluster formation, peer resolution, request routing, internal RPC.
- `cluster/fsm/`, `cluster/dynusers/`, `cluster/namespaces/`, `cluster/distributedtask/`, `cluster/usage/`.

Sharding & replica business logic lives in `usecases/sharding/` and `usecases/replica/`.

---

## 7. Business logic — `usecases/` (VERIFIED, full list)

The "core" domain logic, independent of transport/storage adapters:

`auth` · `backup` · `build` (version metadata) · `byteops` (alloc-free byte ops) · `classification` · `cluster` · `config` (all env config — see §9) · `connstate` · `cron` · `distributedtask` · `export` · `file` · `floatcomp` · `integrity` · `logrusext` · `memwatch` (memory pressure / readonly) · `mmap` · `modulecomponents` · `modules` (module registry) · `monitoring` (Prometheus) · `multitenancy` · `namespace_cleanup` · `namespaces` · `nodes` · `object_ttl` · `objects` (object CRUD) · `ratelimiter` · `replica` · `restrictions` · `schema` (schema manager) · `sharding` · `telemetry` · `traverser` (query planning/execution) · `usagelimits` · `vectorizer`.

Two especially important ones for query work:
- **`usecases/traverser/`** — turns API queries (GraphQL/gRPC) into execution plans against indexes (Get, Aggregate, hybrid fusion, etc.).
- **`usecases/schema/`** — authoritative collection/property/tenant schema management (backed by RAFT).

---

## 8. Domain models — `entities/` (VERIFIED, full list)

The shared types & interfaces (the hexagonal "ports"):

`additional` · `aggregation` · `autocut` · `backup` · `classcache` · `concurrency` · `config` · `cron` · `cyclemanager` · `deepcopy` · `diskio` · `dto` · `errorcompounder` · `errors` (incl. `go_wrapper.go`) · `export` · `filters` · `interval` · `inverted` · `loadlimiter` · `lsmkv` · **`models`** (the OpenAPI-generated wire models) · `modelsext` · **`modulecapabilities`** (module interfaces) · **`moduletools`** · `multi` · `replication` · `schema` · `search` · `searchparams` · `sentry` · `storagestate` · **`storobj`** (on-disk object representation) · `sync` · `tenantactivity` · `tokenizer` · **`vectorindex`** (HNSW/flat/dynamic config) · `verbosity` · `versioned`.

### Module interfaces (VERIFIED — `entities/modulecapabilities/`)
`module.go` (base `Module`: `Name()`, `Init()`, `Type()`), `additional.go`, `backup.go`, `classification.go`, `client.go`, `config.go`, `generative.go`, `graphql.go`, `offload.go`. A module **opts into capabilities** by implementing the relevant interface(s).

---

## 9. Configuration — environment variables (VERIFIED — `usecases/config/`)

Weaviate is configured almost entirely via **environment variables** (Docker/K8s) plus per-collection config sent over the API. Below is the full set extracted from `usecases/config/*.go` and `entities/config/*.go`, grouped by concern. (~200 vars. Exact semantics/defaults live in `usecases/config/config.go`; treat this as the index, and read the source for precise parsing/defaults.)

### Core / persistence
`PERSISTENCE_DATA_PATH` · `PERSISTENCE_LSM_ACCESS_STRATEGY` · `PERSISTENCE_LSM_MAX_SEGMENT_SIZE` · `PERSISTENCE_LSM_SEGMENTS_CLEANUP_INTERVAL_HOURS` · `PERSISTENCE_LSM_SEPARATE_OBJECTS_COMPACTIONS` · `PERSISTENCE_LSM_ENABLE_SEGMENTS_CHECKSUM_VALIDATION` · `PERSISTENCE_LSM_SKIP_WRITE_CLASSNAME_ENABLED` · `PERSISTENCE_LSM_CYCLEMANAGER_ROUTINES_FACTOR` · `PERSISTENCE_MEMTABLES_MAX_SIZE_MB` · `PERSISTENCE_MEMTABLES_FLUSH_DIRTY_AFTER_SECONDS` · `PERSISTENCE_MEMTABLES_FLUSH_IDLE_AFTER_SECONDS` · `PERSISTENCE_MEMTABLES_MAX_ACTIVE_DURATION_SECONDS` · `PERSISTENCE_MEMTABLES_MIN_ACTIVE_DURATION_SECONDS` · `PERSISTENCE_FLUSH_IDLE_MEMTABLES_AFTER` · `PERSISTENCE_MAX_REUSE_WAL_SIZE` · `PERSISTENCE_MIN_MMAP_SIZE` · `PERSISTENCE_LAZY_SEGMENTS_DISABLED` · `PERSISTENCE_SEGMENT_INFO_FROM_FILE_DISABLED` · `PERSISTENCE_WRITE_METADATA_FILES_ENABLED`

### HNSW snapshots / persistence
`PERSISTENCE_HNSW_MAX_LOG_SIZE` · `PERSISTENCE_HNSW_DISABLE_SNAPSHOTS` · `PERSISTENCE_HNSW_SNAPSHOT_INTERVAL_SECONDS` · `PERSISTENCE_HNSW_SNAPSHOT_ON_STARTUP` · `PERSISTENCE_HNSW_SNAPSHOT_MIN_DELTA_COMMITLOGS_NUMBER` · `PERSISTENCE_HNSW_SNAPSHOT_MIN_DELTA_COMMITLOGS_SIZE_PERCENTAGE`

### Vector index runtime / defaults
`DEFAULT_VECTOR_INDEX` · `ALLOWED_VECTOR_INDEX_TYPES` · `DEFAULT_VECTOR_DISTANCE_METRIC` · `DEFAULT_QUANTIZATION` · `ALLOWED_COMPRESSION_TYPES` · `ASYNC_INDEXING` · `HNSW_ACORN_FILTER_RATIO` · `HNSW_FLAT_SEARCH_CONCURRENCY` · `HNSW_GEO_INDEX_EF` · `HNSW_STARTUP_WAIT_FOR_VECTOR_CACHE` · `HNSW_VISITED_LIST_POOL_MAX_SIZE` · `TRACK_VECTOR_DIMENSIONS` · `TRACK_VECTOR_DIMENSIONS_INTERVAL` · `DIMENSION_METRICS_DISABLED`

### Modules / vectorization
`ENABLE_MODULES` · `DEFAULT_VECTORIZER_MODULE` · `API_BASED_MODULES_DISABLED` · `MODULES_CLIENT_TIMEOUT` · `CONTEXTIONARY_URL` (+ per-module URLs like `MODEL2VEC_INFERENCE_API`, `TRANSFORMERS_INFERENCE_API`, etc. — see each module's README)

### Authentication
`AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED` · `AUTHENTICATION_APIKEY_ENABLED` · `AUTHENTICATION_APIKEY_ALLOWED_KEYS` · `AUTHENTICATION_APIKEY_USERS` · `AUTHENTICATION_DB_USERS_ENABLED` · OIDC: `AUTHENTICATION_OIDC_ENABLED` · `..._ISSUER` · `..._CLIENT_ID` · `..._USERNAME_CLAIM` · `..._GROUPS_CLAIM` · `..._SCOPES` · `..._JWKS_URL` · `..._CERTIFICATE` · `..._SKIP_CLIENT_ID_CHECK` · `..._INSECURE_SKIP_TLS_VERIFY` · `..._GLOBAL_PRINCIPAL_CLAIM` · `..._NAMESPACE_CLAIM`

### Authorization (RBAC + adminlist)
`AUTHORIZATION_ENABLE_RBAC` / `AUTHORIZATION_RBAC_ENABLED` · `AUTHORIZATION_RBAC_ROOT_USERS` · `AUTHORIZATION_RBAC_ROOT_GROUPS` · `AUTHORIZATION_RBAC_READONLY_GROUPS` · `AUTHORIZATION_RBAC_IP_IN_AUDIT_LOG_DISABLED` · `AUTHORIZATION_ADMIN_USERS` · `AUTHORIZATION_ADMINLIST_ENABLED` · `AUTHORIZATION_ADMINLIST_USERS` · `AUTHORIZATION_ADMINLIST_GROUPS` · `AUTHORIZATION_ADMINLIST_READONLY_USERS` · `AUTHORIZATION_ADMINLIST_READONLY_GROUPS` (+ `EXPERIMENTAL_AUTHORIZATION_RBAC_*`)

### Cluster / gossip
`CLUSTER_HOSTNAME` · `CLUSTER_BIND_ADDR` · `CLUSTER_ADVERTISE_ADDR` · `CLUSTER_ADVERTISE_PORT` · `CLUSTER_GOSSIP_BIND_PORT` · `CLUSTER_DATA_BIND_PORT` · `CLUSTER_JOIN` · `CLUSTER_IN_LOCALHOST` · `CLUSTER_BASIC_AUTH_USERNAME` · `CLUSTER_BASIC_AUTH_PASSWORD` · `CLUSTER_IGNORE_SCHEMA_SYNC` · `CLUSTER_SKIP_SCHEMA_REPAIR` · `FAST_FAILURE_DETECTION` · `MEMBERLIST_FAST_FAILURE_DETECTION`

### RAFT
`RAFT_PORT` · `RAFT_INTERNAL_RPC_PORT` · `RAFT_JOIN` · `RAFT_BOOTSTRAP_EXPECT` · `RAFT_BOOTSTRAP_TIMEOUT` · `RAFT_HEARTBEAT_TIMEOUT` · `RAFT_ELECTION_TIMEOUT` · `RAFT_LEADER_LEASE_TIMEOUT` · `RAFT_SNAPSHOT_INTERVAL` · `RAFT_SNAPSHOT_THRESHOLD` · `RAFT_TRAILING_LOGS` · `RAFT_TIMEOUTS_MULTIPLIER` · `RAFT_CONSISTENCY_WAIT_TIMEOUT` · `RAFT_GRPC_MESSAGE_MAX_SIZE` · `RAFT_METADATA_ONLY_VOTERS` · `RAFT_ENABLE_ONE_NODE_RECOVERY` · `RAFT_FORCE_ONE_NODE_RECOVERY` · `RAFT_DRAIN_SLEEP`

### Replication
`REPLICATION_MINIMUM_FACTOR` · `REPLICATION_MAXIMUM_FACTOR` · `REPLICATION_GRPC_ENABLED` · `REPLICATION_FORCE_DELETION_STRATEGY` · `REPLICA_MOVEMENT_ENABLED` · `REPLICATION_ENGINE_MAX_WORKERS` · `REPLICATION_ENGINE_FILE_COPY_WORKERS` · `REPLICATION_ENGINE_FILE_COPY_CHUNK_SIZE` · `FORCE_FULL_REPLICAS_SEARCH` · `REPLICATED_INDICES_REQUEST_QUEUE_*` (enabled/size/num_workers/full_http_status/shutdown_timeout) · async replication: `ASYNC_REPLICATION_DISABLED` and the large `ASYNC_REPLICATION_*` family (frequency, hashtree height/concurrency, propagation batch/concurrency/limit/timeouts, scheduler workers)

### Query limits & behavior
`QUERY_DEFAULTS_LIMIT` · `QUERY_DEFAULTS_LIMIT_GRAPHQL` · `QUERY_MAXIMUM_RESULTS` · `QUERY_HYBRID_MAXIMUM_RESULTS` · `QUERY_CROSS_REFERENCE_DEPTH_LIMIT` · `QUERY_NESTED_CROSS_REFERENCE_LIMIT` · `QUERY_BOOST_DEFAULT_DEPTH` · `QUERY_SLOW_LOG_ENABLED` · `QUERY_SLOW_LOG_THRESHOLD` · `QUERY_BITMAP_BUFS_MAX_BUF_SIZE` · `QUERY_BITMAP_BUFS_MAX_MEMORY` · `USE_INVERTED_SEARCHABLE` · `WEAVIATE_PREVIEW_NESTED_FILTERING`

### Auto-schema
`AUTOSCHEMA_ENABLED` · `AUTOSCHEMA_DEFAULT_STRING` · `AUTOSCHEMA_DEFAULT_NUMBER` · `AUTOSCHEMA_DEFAULT_DATE`

### Multi-tenancy / namespaces / TTL
`NAMESPACES_ENABLED` · `NAMESPACE_CLEANUP_INTERVAL` · `LAZY_LOAD_SHARD_COUNT_THRESHOLD` · `LAZY_LOAD_SHARD_SIZE_THRESHOLD_GB` · `DISABLE_LAZY_LOAD_SHARDS` · `TENANT_ACTIVITY_READ_LOG_LEVEL` · `TENANT_ACTIVITY_WRITE_LOG_LEVEL` · Object TTL: `OBJECTS_TTL_BATCH_SIZE` · `OBJECTS_TTL_CONCURRENCY_FACTOR` · `OBJECTS_TTL_DELETE_SCHEDULE` · `OBJECTS_TTL_PAUSE_DURATION` · `OBJECTS_TTL_PAUSE_EVERY_NO_BATCHES`

### Limits / safety / resource pressure
`MAXIMUM_ALLOWED_COLLECTIONS_COUNT` · `MAXIMUM_ALLOWED_OBJECTS_COUNT` · `MAXIMUM_ALLOWED_SHARDS_PER_COLLECTION` · `MAXIMUM_ALLOWED_TENANTS_PER_COLLECTION` · `MAXIMUM_CONCURRENT_GET_REQUESTS` · `MAXIMUM_CONCURRENT_SHARD_LOADS` · `MAXIMUM_CONCURRENT_BUCKET_LOADS` · `MAX_IMPORT_GOROUTINES_FACTOR` · `MEMORY_READONLY_PERCENTAGE` · `MEMORY_WARNING_PERCENTAGE` · `DISK_USE_READONLY_PERCENTAGE` · `DISK_USE_WARNING_PERCENTAGE` · `USAGE_LIMITS_ERROR_MESSAGE` · `RESTRICTIONS_ERROR_MESSAGE`

### Backup / export / usage
`BACKUP_CHUNK_TARGET_SIZE` · `BACKUP_MIN_CHUNK_SIZE` · `BACKUP_SPLIT_FILE_SIZE` · `EXPORT_ENABLED` · `EXPORT_DEFAULT_PATH` · `EXPORT_DEFAULT_BUCKET` · `EXPORT_PARALLELISM`

### Server / transport
`GRPC_PORT` · `GRPC_MAX_MESSAGE_SIZE` · `GRPC_MAX_OPEN_CONNS` · `GRPC_IDLE_CONN_TIMEOUT` · `GRPC_CERT_FILE` · `GRPC_KEY_FILE` · `DISABLE_GRAPHQL` · `CORS_ALLOW_ORIGIN` · `CORS_ALLOW_METHODS` · `CORS_ALLOW_HEADERS` · `MINIMUM_INTERNAL_TIMEOUT`

### Monitoring / profiling / telemetry
`PROMETHEUS_MONITORING_ENABLED` · `PROMETHEUS_MONITORING_PORT` · `PROMETHEUS_MONITORING_GROUP` · `PROMETHEUS_MONITORING_GROUP_CLASSES` · `PROMETHEUS_MONITORING_METRIC_NAMESPACE` · `PROMETHEUS_MONITOR_CRITICAL_BUCKETS_ONLY` · `GO_PROFILING_PORT` · `GO_PROFILING_DISABLE` · `GO_BLOCK_PROFILE_RATE` · `GO_MUTEX_PROFILE_FRACTION` · `DISABLE_TELEMETRY` · `TELEMETRY_URL` · `TELEMETRY_PUSH_INTERVAL`

### Startup reindex / maintenance
`REINDEX_INDEXES_AT_STARTUP` · `REINDEX_VECTOR_DIMENSIONS_AT_STARTUP` · `REINDEX_SET_TO_ROARINGSET_AT_STARTUP` · `REINDEX_CONCURRENCY` · `REINDEXER_GOROUTINES_FACTOR` · `INDEX_MISSING_TEXT_FILTERABLE_AT_STARTUP` · `INDEX_RANGEABLE_IN_MEMORY` · `INVERTED_SORTER_DISABLED` · `RECOUNT_PROPERTIES_AT_STARTUP` · `REVECTORIZE_CHECK_DISABLED` · `MAINTENANCE_NODES` · `TRANSFER_INACTIVITY_TIMEOUT`

### Runtime overrides / experimental / sharding
`RUNTIME_OVERRIDES_ENABLED` · `RUNTIME_OVERRIDES_PATH` · `RUNTIME_OVERRIDES_LOAD_INTERVAL` · `DEFAULT_SHARDING_COUNT` · `OPERATIONAL_MODE` · `MCP_SERVER_ENABLED` · `MCP_SERVER_CONFIG_PATH` · `MCP_SERVER_WRITE_ACCESS_ENABLED` · `EXPERIMENTAL_METADATA_SERVER_ENABLED` · `EXPERIMENTAL_METADATA_SERVER_GRPC_LISTEN_ADDRESS` · `EXPERIMENTAL_METADATA_SERVER_DATA_EVENTS_CHANNEL_CAPACITY` · `DISTRIBUTED_TASKS_SCHEDULER_TICK_INTERVAL_SECONDS` · `DISTRIBUTED_TASKS_COMPLETED_TASK_TTL_HOURS`

> Note: env var **defaults and validation** are in `usecases/config/config.go`. Per-collection settings (vectorizer, vector index type/params, inverted-index config, sharding, replication factor, multi-tenancy, BM25 params) are sent via the API at collection-creation time, not env — see the client's `Configure` builders.

---

## 10. Modules — the plugin system (VERIFIED — 68 module dirs in `modules/`)

Modules implement the `Module` interface (`Name()`, `Init()`, `Type()` from `entities/modulecapabilities/module.go`) and optionally provide vectorization, generative, reranking, backup, or HTTP-handler capabilities. They are **registered in `adapters/handlers/rest/configure_api.go`** and enabled at runtime via `ENABLE_MODULES`.

Naming convention encodes the capability:
- `text2vec-*` — text embedding vectorizers
- `multi2vec-*` / `multi2multivec-*` — multimodal / multi-vector embedders
- `text2multivec-*` — text → multi-vector (late interaction)
- `img2vec-*` — image embedders
- `generative-*` — RAG / generative answer modules
- `reranker-*` — rerankers
- `qna-*`, `sum-*`, `ner-*`, `text-spellcheck` — task-specific NLP
- `ref2vec-*` — reference-based vectorization (centroid of cross-refs)
- `backup-*` — backup backends
- `offload-*`, `usage-*` — tenant offloading / usage metering

### Full module inventory (VERIFIED)
**Vectorizers — text2vec:** `text2vec-openai`, `-cohere`, `-huggingface`, `-google`, `-jinaai`, `-voyageai`, `-mistral`, `-nvidia`, `-aws`, `-databricks`, `-ollama`, `-gpt4all`, `-transformers`, `-contextionary`, `-model2vec`, `-weaviate`, `-bigram`, `-morph`, `-octoai`, `-digitalocean`.

**Multimodal / multi-vector:** `multi2vec-clip`, `-cohere`, `-google`, `-jinaai`, `-voyageai`, `-nvidia`, `-aws`, `-bind`, `-dummy`; `multi2multivec-jinaai`, `multi2multivec-weaviate`; `text2multivec-jinaai`; `img2vec-neural`; `ref2vec-centroid`.

**Generative (RAG):** `generative-openai`, `-anthropic`, `-cohere`, `-google`, `-mistral`, `-aws`, `-nvidia`, `-ollama`, `-databricks`, `-anyscale`, `-friendliai`, `-octoai`, `-contextualai`, `-xai`, `-dummy`.

**Rerankers:** `reranker-cohere`, `-jinaai`, `-voyageai`, `-nvidia`, `-transformers`, `-contextualai`, `-dummy`.

**Task NLP:** `qna-openai`, `qna-transformers`, `sum-transformers`, `ner-transformers`, `text-spellcheck`.

**Backup backends:** `backup-s3`, `backup-gcs`, `backup-azure`, `backup-filesystem`.

**Offload / usage:** `offload-s3`, `usage-gcs`, `usage-s3`.

> Many `*-transformers`, `*-clip`, `*-model2vec`, `*-gpt4all`, `*-contextionary` modules require a **companion inference container** (set its URL, e.g. `MODEL2VEC_INFERENCE_API`, `TRANSFORMERS_INFERENCE_API`). API-based modules (OpenAI, Cohere, etc.) require provider **API keys**, supplied per-request as headers or via env.

---

## 11. Repo file/dir map — quick reference (VERIFIED)

| Path | Purpose |
|---|---|
| `cmd/weaviate-server/` | Main entry point (go-swagger generated) |
| `adapters/handlers/rest/` | REST handlers + `configure_api.go` (`MakeAppState()` startup wiring) |
| `adapters/handlers/grpc/` | gRPC handlers (search/batch/aggregation) |
| `adapters/handlers/graphql/` | GraphQL layer |
| `adapters/handlers/mcp/` | MCP server handlers |
| `adapters/clients/` | Outbound HTTP clients to module inference services |
| `adapters/repos/db/` | Storage engine: index/shard/CRUD/batch/backup/hybrid/BM25 |
| `adapters/repos/db/lsmkv/` | Custom LSM KV store (memtables, WAL, compaction, cursors) |
| `adapters/repos/db/vector/` | HNSW / flat / dynamic indexes + compression + multivector |
| `adapters/repos/schema/`, `.../classifications/`, `.../modules/`, `.../transactions/` | Schema, classification, module, txn persistence |
| `usecases/` | Business logic (schema, objects, traverser, backup, config, …) |
| `entities/` | Domain models, interfaces, generated wire models, vectorindex config |
| `modules/` | 68 plugin modules (vectorizers/generative/rerankers/backup/usage) |
| `cluster/` | RAFT consensus, replication, sharding, RBAC, schema-as-state |
| `grpc/proto/v1` (+ `v0`) | Protobuf definitions |
| `openapi-specs/` | OpenAPI source for REST codegen |
| `client/` | Go client package |
| `docs/` | Topic-specific design notes (check before working an unfamiliar area) |
| `test/` | Unit/integration/acceptance (testcontainers) suites |
| `tools/` | Codegen + linters (`gen-code-from-swagger.sh`, `linter_go_routines.sh`) |
| `.claude/scripts/` | CI/PR monitoring scripts used by maintainers |
| `docker-compose*.yml`, `Dockerfile`, `Makefile` | Build/run/deploy |
| `CLAUDE.md` | Maintainers' architecture + conventions brief (authoritative) |

---

## 12. Build, run, test (VERIFIED — `CLAUDE.md` + `Makefile`)

```bash
make weaviate          # build static binary (CGO_ENABLED=0)
make weaviate-debug    # debug binary (delve)
make local             # run locally (starts deps via docker-compose)
make weaviate-image    # build Docker image
make grpc              # regenerate gRPC protobuf code (buf)
make mocks             # regenerate mocks (mockery via Docker)
```
Tests:
```bash
go test ./adapters/repos/db/lsmkv/...                       # one package
go test -tags integrationTest -count 1 -race ./adapters/repos/db/...   # integration
go test -count 1 -race -timeout 15m ./test/acceptance/grpc/...         # acceptance (testcontainers)
golangci-lint run ./... && ./tools/linter_go_routines.sh               # lint (required)
```
Conventions: table-driven tests; never run the full suite repo-wide; pre-build the image and pass `TEST_WEAVIATE_IMAGE` for acceptance tests; **golangci-lint v2 + gofumpt**; goroutines must use the `entities/errors` wrapper.

### Minimal Docker run (VERIFIED — README)
```yaml
services:
  weaviate:
    image: cr.weaviate.io/semitechnologies/weaviate:1.36.0
    ports: ["8080:8080", "50051:50051"]
    environment:
      ENABLE_MODULES: text2vec-model2vec
      MODEL2VEC_INFERENCE_API: http://text2vec-model2vec:8080
  text2vec-model2vec:
    image: cr.weaviate.io/semitechnologies/model2vec-inference:minishlab-potion-base-32M
```
Ports: **8080** (HTTP/REST/GraphQL), **50051** (gRPC).

---

## 13. AI agent skills (VERIFIED — README)
Weaviate publishes a dedicated agent-skills collection for coding agents (Claude Code, Cursor, Copilot): **`github.com/weaviate/agent-skills`**, covering searching, querying, collection management, data import, and full app blueprints (RAG, agentic RAG, chatbots). Install: `npx skills add weaviate/agent-skills`. (Recommended to install alongside this context file — it gives task-level recipes this file deliberately doesn't duplicate.)

---

## 14. Recommendations for your project
1. **Use the maintainers' `CLAUDE.md` and `docs/` as ground truth** when extending Weaviate — it documents the hexagonal layering, the DB→Index→Shard→Store path, and conventions (byteops, goroutine wrapper, logging) that linters enforce.
2. **For server boot / how-things-wire-together questions, start at `adapters/handlers/rest/configure_api.go:MakeAppState()`.** Everything is constructed there.
3. **For query work, read `usecases/traverser/` + `adapters/handlers/grpc/`;** gRPC is the high-performance search path, GraphQL/REST sit alongside.
4. **For storage/perf work, the LSM (`adapters/repos/db/lsmkv/`) and vector indexes (`adapters/repos/db/vector/`)** are where it matters; respect the alloc-free hot-path rules.
5. **Pin the image tag** (e.g. `1.36.x`) and keep this file's version note in mind — config flags and module lists drift between minor releases; re-extract env vars from `usecases/config/` when you upgrade.
6. **Choose the vector index deliberately:** `flat` for tiny/per-tenant data, `hnsw` for scale, `dynamic` to auto-switch. Enable quantization (PQ/BQ/SQ/RQ) to cut memory once recall is validated.
7. **Treat module enablement as two concerns:** (a) `ENABLE_MODULES` + provider keys for API modules; (b) a companion inference container + its `*_INFERENCE_API` URL for local-model modules.

---

## 15. Caveats
- **Fast-moving project.** Env vars, defaults, and the module list are read from `main` at commit `82695961…`; the newest tagged release is **v1.36.2**. Some flags above (e.g. `EXPERIMENTAL_*`, `NAMESPACES_*`, MCP server) are experimental/preview and may change or be gated. Re-verify against the exact version you deploy.
- **Defaults vs presence:** §9 lists the *full set of recognized variables*; precise defaults and parsing live in `usecases/config/config.go`. A few entries in the raw extraction are token artifacts (e.g. `TRUE`, `POST`, `INFO`, `ENABLED`, `NOLIMIT`, `UNLIMITED`, `TEST_*`) and are intentionally omitted above.
- **Per-collection config is not env-based** — vectorizer, index type/params, BM25, sharding, replication factor, and multi-tenancy are set through the API at collection creation (client `Configure` builders), and override server defaults.
- **Module count:** 68 directories under `modules/`; the README/`CLAUDE.md` say "~67" — the discrepancy is normal churn as modules are added. Trust the directory listing in §10 for `main`.
- The repo's `CLAUDE.md` quotes are reproduced as short factual excerpts; for exact wording read the file in-repo.

---

## 16. Source links
- Repo: `https://github.com/weaviate/weaviate`
- Site: `https://weaviate.io/`
- Docs: `https://docs.weaviate.io/`
- Go reference: `https://pkg.go.dev/github.com/weaviate/weaviate`
- Agent skills: `https://github.com/weaviate/agent-skills`
- REST API: `https://docs.weaviate.io/weaviate/api/rest` · gRPC: `https://docs.weaviate.io/weaviate/api/grpc` · GraphQL: `https://docs.weaviate.io/weaviate/api/graphql`
- Model providers: `https://docs.weaviate.io/weaviate/model-providers`
- Deploy: `https://docs.weaviate.io/deploy`
