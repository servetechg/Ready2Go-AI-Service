# Ready2Go (earth-quick-alert) — Project Context for Python AI Service

> **Audience:** the Python team building a separate AI service to replace the in-app
> COOP/BC integrity scoring + summary pipeline with a vector-DB-backed implementation.
>
> **Scope:** everything you need to design, build, integrate, and roll out that
> service without cloning this repo. Code excerpts are quoted verbatim from
> source so this document is self-contained.
>
> **Repo:** `earth-quick-alert`  ·  **Product name:** **Ready2Go**
> **Framework:** Next.js 15 (App Router) · React 19 · TypeScript 5 · MongoDB / Mongoose 8
> **Doc generated:** 2026-05-30

---

## §0 — TL;DR / Quick Start

Ready2Go is an emergency-management SaaS. One of its admin pages lets operators
upload **continuity-vault documents** (COOP Protocols, Business Continuity, Compliance
Vault, Response Plans). On upload the app extracts text, asks OpenAI to score
"AI Integrity" (`In Sync` / `Reviewing` / `Deviation Found`) + write a one-liner
summary, then stores both on the file's MongoDB subdocument. A separate aggregate
("AI-Driven Continuity Audit") summarizes the whole vault.

The current pipeline is a **single OpenAI HTTP call per upload, no chunking, no
caching, no vector store, no retries**. The Python service is the planned
replacement.

### The 5 facts that matter most

1. **Per-file integration surface = MongoDB `EmergencyPlan.attachments[]`
   subdocument.** Python writes back:
   - `aiIntegrityStatus` ∈ `"In Sync" | "Reviewing" | "Deviation Found"` (exact strings — case + spacing matter)
   - `aiIntegrityScore` ∈ `[0, 100]` (integer)
   - `aiIntegritySummary` ≤ 280 chars
   - `aiIntegrityAnalyzedAt` (Date)
2. **Aggregate surface = MongoDB `ContinuityAudit` singleton** (`scope: 'global'`,
   unique). Fields: `summary`, `findings[]`, `posture ∈ {Resilient, Steady, At
   Risk}`, `averageScore`, `totals`, `integrity`, `generatedAt`.
3. **Files live in Cloudinary** at folder `earthquick/emergency-plans`.
   Fetch via `attachment.fileUrl` (the Cloudinary `secure_url`).
4. **Allowed file types** for this module: PDF, DOCX, XLSX, CSV. 25 MB cap.
   Current text-extraction cap is **8 000 chars** at upload time.
5. **Current LLM** = OpenAI Chat Completions via direct `fetch()` (no SDK).
   Model is `process.env.OPENAI_MODEL` (default `gpt-4o-mini`). API key
   `process.env.OPENAI_API_KEY`.

### Where to look in this document

| Need | Section |
|------|---------|
| Exact MongoDB schema + write-back contract | §4, §6 |
| Current AI prompt + code (what you are replacing) | §6 |
| Other AI/scoring code in this repo (so you don't duplicate) | §6.5 |
| Two transport options for Next.js → Python | §11 |
| Rollout / coexistence strategy | §11.5 |
| Exact enum strings — case-sensitive | §12 |

---

## §1 — Project Overview

Ready2Go is a multi-role emergency-management platform serving four primary user
audiences:

- **Super-admin / admin / sub-admin / manager / observer** — operational
  dashboards for incident handling, COOP/BC continuity planning, AI risk
  assessment, after-action review, alert dispatch.
- **EOC manager / EOC observer** — Virtual EOC (Emergency Operations Center)
  console: command center, weather/traffic, maintenance, lodging, recovery.
- **Responder / public_official** — vertical-specific deployment dashboards
  (police, hospital, electric, gas, water, transit, nonprofit, food-logistics,
  national-guard, federal, pharmacy, energy).
- **User** — citizen-facing app: alerts, weather, personal plan, family
  status, preparedness checklists, favorite places.

### Capabilities → URL map

| Capability | URL (admin) |
|------------|-------------|
| Dashboard | `/admin-dashboard`, `/super-admin-dashboard` |
| **COOP / BC Plans** (Python team's primary surface) | `/emergency-plan` |
| Alerts & Communication | `/alerts-communication` |
| Emergency events | `/emergency-events` |
| AI Risk Assessment | `/ai-risk-assessment` |
| GIS Mapping | `/gis-mapping` |
| After Action Review | `/after-action-review` |
| Preparedness Information | `/preparedness-information` |
| Responders & Agencies | `/responders-agencies` |
| Virtual EOC | `/virtual-eoc` (+ `/center`, `/weather-traffic`, `/maintenance`, `/lodging`, `/recovery`) |
| Virtual EOC AI Center | `/virtual-eoc-ai-center` |
| Settings | `/settings`, `/sub-admin-settings`, `/virtual-eoc-settings`, `/responder-settings` |

### Tech stack one-pager

- **Framework**: Next.js 15.1.9 (App Router), React 19.2, TypeScript 5
- **DB**: MongoDB (Atlas) via Mongoose 8.9
- **Auth**: JWT (`jose`) in httpOnly cookie + `bcryptjs` for password hashing
- **Storage**: Cloudinary (folder `earthquick/emergency-plans` for plan files)
- **AI**: OpenAI Chat Completions via direct `fetch()` (no SDK)
- **Maps**: deck.gl + `@react-google-maps/api` + Leaflet
- **UI**: Radix UI primitives + Tailwind CSS 4
- **Charts**: Recharts
- **File parsing**: `pdf-parse`, `mammoth`, `xlsx`, `jspdf`, `fast-xml-parser`
- **Mail**: nodemailer
- **Realtime / queue (declared but UNUSED)**: `socket.io`, `socket.io-client`,
  `ioredis`. The `server/workers/` directory referenced by npm scripts does not
  exist in the repo.

---

## §2 — Repository Layout

Top-level annotated map (relevant directories only):

```
earth-quick-alert/
├── app/                         Next.js App Router root
│   ├── (admin)/                 Admin pages (sidebar + admin shell)
│   │   ├── emergency-plan/        ← COOP/BC Plans page (the Python team's surface)
│   │   ├── ai-risk-assessment/
│   │   ├── alerts-communication/
│   │   ├── admin/{users,licenses,sub-admins}/
│   │   ├── virtual-eoc/{,center,weather-traffic,maintenance,lodging,recovery}/
│   │   ├── responder-{dashboard,field-status,lodging-status,settings}/
│   │   ├── responders-agencies/, after-action-review/, gis-mapping/, etc.
│   ├── (user)/                  Citizen-facing pages
│   ├── api/                     Server route handlers (≈ 95 files; see §5)
│   ├── login/, signup/, pending-approval/, page.tsx
├── components/                  React components
│   ├── ui/                      Radix-based primitives (~40 files)
│   ├── modals/                  Cross-page modals
│   ├── admin-dashboard/, responder/, preparedness/
│   ├── providers/               Auth context provider
│   └── (root)                   Page shells, headers, sidebars, maps, charts
├── lib/
│   ├── services/                Business-logic services (incl. openai-service.ts)
│   ├── auth.ts                  jose JWT helpers
│   ├── mongodb.ts               Mongoose connection (cached global)
│   ├── cloudinary.ts            Cloudinary SDK config
│   ├── emergency-plan-cloudinary.ts   Upload helper for plan files
│   ├── emergency-plan-ai-integrity.ts Text extraction (PDF/DOCX/XLSX/CSV)
│   ├── activity-log.ts, activity-actions.ts   Audit-trail writer
│   ├── responder-verticals.ts   Canonical responder vertical list
│   ├── normalization/           External-feed → UnifiedEvent mappers
│   ├── unified-event/           UnifiedEvent domain helpers
│   ├── risk-assessment/         AI risk-assessment helpers
│   ├── scoring/                 Deterministic scoring helpers
│   ├── notification-preferences/, preparedness-tasks/, hooks/, store/, types/, utils/
├── models/                      38 Mongoose schemas (see §4)
├── middleware.ts                Role/route gating
├── next.config.mjs              Next.js config (TS errors ignored)
├── tsconfig.json
├── seeddata.js                  Local dev seeding script
├── .env                         Env var values (gitignored)
├── PROJECT_ARCHITECTURE.md      Older architecture doc (partly stale)
├── ARCHITECTURE_DIAGRAMS.md     Mermaid diagrams (partly stale)
├── doc/                         Operational notes
├── docs/                        Active specs (incl. this file)
├── plan/                        Sprint planning notes
├── pages/                       LEGACY Pages Router (mostly empty)
├── public/                      Static assets
├── hooks/, scratch/, testsprite_tests/
```

**Root JSON files** (`model.json`, `nws_active_alerts.json`,
`usgs_river_data.json`, `noaa_nwps_gauges.json`, `nasa_firms_fires.json`) are
**dev fixtures / snapshots**, not live data. Ignore them.

---

## §3 — Tech Stack & Runtime

### Dependencies (grouped)

| Group | Packages |
|-------|----------|
| Data | `mongoose`, `bcryptjs`, `jose`, **`ioredis` (unused)** |
| File parsing | `pdf-parse`, `mammoth`, `xlsx`, `jspdf`, `fast-xml-parser` |
| Cloud / mail | `cloudinary`, `nodemailer` |
| Maps | `@deck.gl/core`, `@deck.gl/aggregation-layers`, `@deck.gl/google-maps`, `@react-google-maps/api`, `leaflet`, `react-leaflet` |
| Charts | `recharts` |
| UI primitives | `@radix-ui/react-*` (~25 packages), `lucide-react`, `cmdk`, `embla-carousel-react`, `react-day-picker`, `react-resizable-panels`, `react-hook-form`, `@hookform/resolvers`, `input-otp`, `vaul`, `sonner` |
| Styling | `tailwindcss@4`, `@tailwindcss/postcss`, `postcss`, `autoprefixer`, `class-variance-authority`, `tailwind-merge`, `tailwindcss-animate`, `tw-animate-css`, `next-themes` |
| Validation / dates / util | `zod`, `date-fns`, `clsx`, `dotenv` |
| Realtime (UNUSED) | `socket.io`, `socket.io-client` |
| Analytics | `@vercel/analytics` |

### Scripts (`package.json`)

```json
"scripts": {
  "build": "next build",
  "dev": "next dev",
  "lint": "eslint .",
  "start": "next start",
  "worker:ingest": "npx tsx server/workers/ingestion-worker.ts",
  "worker:stream":  "npx tsx server/workers/stream-worker.ts"
}
```

**⚠ The two `worker:*` scripts are stubs** — `server/workers/` does not exist
in the repo.

### Runtime config

`next.config.mjs` (loose for now):
```js
{ typescript: { ignoreBuildErrors: true }, images: { unoptimized: true } }
```

`tsconfig.json`: strict mode, ES6 target, path alias `@/* → ./*`.

### Environment variables (names only)

Variables actually present in `.env` of this repo:

```
MONGODB_URI=<value>
JWT_SECRET=<value>
GOOGLE_MAPS_API_KEY=<value>
OPENAI_API_KEY=<value>
OPENAI_MODEL=<value>
INCIWEB_SYNC_ENABLED=<value>
NASA_FIRMS_MAP_KEY=<value>
CLOUDINARY_CLOUD_NAME=<value>
CLOUDINARY_API_KEY=<value>
CLOUDINARY_API_SECRET=<value>
```

Additional env vars referenced in code (group → name):

- **DB**: `MONGODB_URI`, `MONGODB_DB` (default DB name = `ready2go`)
- **Auth**: `JWT_SECRET` (fallback: hard-coded dev string — never reuse in prod)
- **AI**: `OPENAI_API_KEY`, `OPENAI_MODEL` (default `gpt-4o-mini`)
- **Cloudinary**: `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`
- **Maps**: `NEXT_PUBLIC_GOOGLE_MAPS_API_KEY`, `GOOGLE_MAPS_API_KEY`, `NEXT_PUBLIC_OPENWEATHER_API_KEY`
- **NWS feed**: `NWS_ALERT_SYNC_ENABLED`, `NWS_USER_AGENT`, `NWS_ALERTS_MAX_PAGES`, `NWS_ALERT_SYNC_LAT`, `NWS_ALERT_SYNC_LON`, `NWS_ALERT_SYNC_SCOPE`, `NWS_SYNC_MIN_INTERVAL_MS`
- **USGS feed**: `USGS_EQ_FDSNWS_MAX_PAGES`, `USGS_SYNC_INCLUDE_NORMAL`, `USGS_SITES`, `RISK_USGS_SITES`, `RISK_EARTHQUAKE_RADIUS_KM`
- **NWPS feed**: `NWPS_REACH_IDS`, `NWPS_GAUGE_LIDS`, `NWPS_SYNC_ENABLED`, `NWPS_USER_AGENT`, `NWPS_SYNC_DEFAULT_GAUGES`, `RISK_NWPS_RADIUS_KM`, `RISK_NWPS_LID_<STATE>`
- **NASA FIRMS feed**: `NASA_FIRMS_API_KEY`, `NASA_FIRMS_MAP_KEY`, `MODIS_MAP_KEY`, `FIRMS_SOURCE`, `FIRMS_BBOX`, `FIRMS_DAYS`, `FIRMS_MAX_CARDS`, `FIRMS_SYNC_INCLUDE_NORMAL`
- **FEMA feed**: `FEMA_OPEN_SYNC_ENABLED`, `FEMA_OPEN_TOP`, `OPENFEMA_MAX_PAGES`, `OPENFEMA_USER_AGENT`, `OPENFEMA_WEB_MAX_PAGES`, `OPENFEMA_WEB_PAGE_SIZE`
- **InciWeb feed**: `INCIWEB_SYNC_ENABLED`, `INCIWEB_RSS_URL`, `INCIWEB_USER_AGENT`, `INCIWEB_REFERER`
- **Multi-feed**: `MULTI_ALERT_SYNC_ENABLED`, `MULTI_ALERT_SYNC_MIN_INTERVAL_MS`, `UNIFIED_EVENT_HISTORICAL_ENABLED`, `CENSUS_API_KEY`
- **Email**: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_SECURE`, `SMTP_FROM`, `RESPONDER_INVITE_SMTP_FROM`, `RESPONDER_INVITE_SMTP_URL`
- **Platform**: `APP_URL`, `NEXT_PUBLIC_APP_URL`, `HTTP_UPSTREAM_USER_AGENT`, `NODE_ENV`, `VERCEL_URL`, `ALERTS_COMMUNICATION_INCLUDE_MANUAL`

---

## §4 — Data Model (MongoDB / Mongoose)

DB name (default): **`ready2go`** (override with `MONGODB_DB`).

The connection helper (`lib/mongodb.ts`) uses a cached global Mongoose
connection to survive hot reloads:

```ts
let cached = (global as any).mongoose;
if (!cached) cached = (global as any).mongoose = { conn: null, promise: null };

async function connectDB() {
  if (cached.conn) return cached.conn;
  if (!cached.promise) {
    cached.promise = mongoose.connect(withDefaultDatabase(MONGODB_URI), { bufferCommands: false });
  }
  cached.conn = await cached.promise;
  return cached.conn;
}
```

**Python team: open a SEPARATE connection** to MongoDB (different driver, own
pool). Do not share the Node app's connection.

### 4.1 — `UnifiedEvent` ⭐ central event store

Used by the AI Risk Assessment page (out-of-scope for integrity service, but
documented so you don't accidentally overlap).

```ts
const UnifiedEventSchema = new Schema({
    externalId: { type: String, required: true, unique: true, index: true },
    source: { type: String, enum: ['nws','usgs','earthquake','nwps','fema','nasa_firms','inciweb','noaa_nwis','noaa_ncei','manual','seed'], required: true, index: true },
    category: { type: String, enum: ['flood','earthquake','wildfire','storm','marine','coastal_surf','hazardous','tsunami','volcanic','landslide','winter_weather','air_quality','extreme_heat','fema_declaration'], required: true, index: true },
    name: { type: String, required: true },
    description: { type: String, default: '' },
    severity: { type: String, enum: ['Low','Moderate','High','Extreme'], default: 'Moderate' },
    type: { type: String, enum: ['Warning','Watch','Advisory','Statement','Declaration'], default: 'Warning' },
    iconType: { type: String, enum: ['cloud','triangle','lightning','flame','wave','snowflake','wind'], default: 'triangle' },
    status: { type: String, enum: ['Take Action','Get Prepared','Monitor','Info'], default: 'Take Action' },
    dataStatus: { type: String, enum: ['current','past'], default: 'current', index: true },
    location: { type: String, required: true },
    lat: { type: Number, default: null },
    lng: { type: Number, default: null },
    coordinates: { type: { lat: Number, lng: Number }, default: null },
    geometry: { type: Schema.Types.Mixed, default: null },
    issuedAt: { type: String, required: true },
    expiresAt: { type: String, required: true },
    instructions: [{ type: String }],
    properties: { type: Schema.Types.Mixed, default: {} },
}, { timestamps: true });

UnifiedEventSchema.index({ source: 1, dataStatus: 1, updatedAt: -1 });
UnifiedEventSchema.index({ category: 1, dataStatus: 1, updatedAt: -1 });
```

### 4.2 — `EmergencyPlan` ⭐ THE integration surface for the Python service

```ts
const EmergencyPlanSchema = new Schema({
    planId: { type: String, required: true, unique: true }, // e.g. 'hurricane_warning'
    label: { type: String, required: true },
    overview: { type: String, required: true },
    category: { type: String, enum: ['coop','bcp','compliance'] },
    steps: [{ type: String }],
    attachments: [{
        fileName: { type: String, required: true },
        fileUrl: { type: String, required: true },         // Cloudinary secure_url
        size: { type: Number },
        uploadedAt: { type: Date, default: Date.now },
        cloudinaryPublicId: { type: String },
        cloudinaryResourceType: { type: String, enum: ['image','raw'] },
        // ⭐ Python service writes these four fields:
        aiIntegrityStatus: { type: String },               // 'In Sync' | 'Reviewing' | 'Deviation Found'
        aiIntegrityScore: { type: Number },                // 0..100
        aiIntegritySummary: { type: String },              // ≤280 chars
        aiIntegrityAnalyzedAt: { type: Date },
    }],
}, { timestamps: true });
```

**Critical:** the UI reads these exact field names. Don't rename, don't add a
v2-suffix unless you're doing shadow rollout.

`category` stored values are only `coop | bcp | compliance`. The UI also shows a
fourth **synthetic** bucket `response` that's inferred client-side from `planId`
regex (see §10) — never store `'response'` in the DB.

### 4.3 — `ContinuityAudit` ⭐ aggregate write-back

Singleton (one document, `scope: 'global'`).

```ts
const ContinuityAuditSchema = new Schema({
    scope: { type: String, default: 'global', unique: true },
    summary: { type: String, default: '' },
    findings: [{ type: String }],
    posture: { type: String, enum: ['Resilient','Steady','At Risk'], default: 'At Risk' },
    averageScore: { type: Number, default: 0 },
    totals: {
        plans: { type: Number, default: 0 },
        attachments: { type: Number, default: 0 },
        analyzed: { type: Number, default: 0 },
    },
    integrity: {
        inSync: { type: Number, default: 0 },
        reviewing: { type: Number, default: 0 },
        deviation: { type: Number, default: 0 },
        unanalyzed: { type: Number, default: 0 },
    },
    generatedAt: { type: Date, default: Date.now },
}, { timestamps: true });
```

Upsert pattern (verbatim from `app/api/admin/emergency-plans/audit-summary/route.ts`):

```ts
await ContinuityAudit.findOneAndUpdate(
  { scope: 'global' },
  { $set: { scope: 'global', summary, findings, posture, averageScore, totals, integrity, generatedAt: now } },
  { upsert: true, new: true }
);
```

### 4.4 — `User`

Auth principal. Tenant scoping is **location-based** (city/state/country), not
org-id-based.

Selected fields:

| Field | Type | Notes |
|-------|------|-------|
| `name` | String, required | |
| `email` | String, required, unique, lowercase | |
| `password` | String, required, min 6, `select: false` | bcrypt hash |
| `role` | enum | **9 roles**: see §12 |
| `licenseId` | ObjectId → `License`, default null | null = unattached |
| `accountStatus` | enum | `pending` / `approved` / `rejected` |
| `isSafe` | Boolean, default true | family-status flag |
| `location` | String | free-text |
| `country`, `state`, `city`, `zipcode` | String | tenant scoping keys |
| `lat`, `lng` | Number, default null | |
| `responderVertical` | enum | see `RESPONDER_VERTICALS`, §12 |
| `responderFunction` | String | free-text |
| `familyMembers[]` | subdocs | name, relationship, location, status enum |
| `emergencyContacts[]` | subdocs | name, phone, relation |
| `supplyKit[]`, `meetingPoints[]`, `preparednessChecklist[]`, `favoritePlaces[]` | subdocs | citizen-side data |
| `notificationPreferences` | subdoc | push/sms/email/majorAlerts/minorAlerts/aiReports booleans |
| `twoFactorEnabled`, `sessionTimeoutEnabled` | Boolean | |
| `requestedLicense`, `requestedOrgName` | mixed | license-request flow |
| `createdBy` | ObjectId → `User`, default null | invite chain |

### 4.5 — All other models (field summary)

| Model | Purpose | Key fields |
|-------|---------|-----------|
| `ActiveEmergency` | Region's currently-declared emergencies | `name, location, city, time, status, subAdminName, timestamps` |
| `ActivityLog` | Audit trail for user actions | `userId→User, action(maxlen 120), label(maxlen 500), meta(Mixed)`; idx `(userId, createdAt desc)` |
| `AlertSentEmergency` | Log of broadcast alerts | Same shape as `ActiveEmergency` |
| `CommunityAlert` | Manual + NWS community alerts | `source(enum), isPinned, severity(enum: critical|extreme|severe|warning|watch|advisory|moderate|minor|info|low|high), title, description, timestamp, expiresAt, affectedAreas[], adminName, adminEmail, targetUsers[], priority(low|medium|high), isRead` |
| `DispatchSettings` | Auto-dispatch defaults | `autoDispatchMajor, autoEscalateMinutes(1..240), defaultChannel(all|sms|email|push), region(western|central|eastern|national), messageTemplate(≤1200), updatedBy→User` |
| `EOCSettings` | Per-license EOC config | `licenseId→License unique, activationThresholds.{minor,moderate,major,catastrophic}.triggerEOC, pollingIntervals.eventTypesForOneMinute[enum], communicationTemplates[], alertFeeds.{nws,local,other}` |
| `EmergencyEvent` | Operator-created event | `type(enum), title, description, status(active|monitoring|resolved|archived), severity, location.{lat,lng,address}, affectedZones[], magnitude, category, windSpeed, createdBy, timeline[{timestamp,action,description,user}], resolvedAt, linkedLicenseId→License, isEOCActivated` |
| `EmergencyPlan` | **COOP/BCP/compliance plans** — see §4.2 | |
| `ContinuityAudit` | **Audit singleton** — see §4.3 | |
| `FieldReport` | Field-collected incident reports | `name, category(enum: Road Closures|Structural Damage|Medical Emergency|Fire/Hazmat|Other), incidentDate, fileReference, status(Review|Reviewed)` |
| `IncidentReport` | User-reported incidents | `type(enum: Road Closure|Downed Tree|Water Main Leak|Power Outage|Other), location, lat?, lng?, description, reportedBy, source(AI Feed|End User), status(Submitted|Active|Crew Dispatched|Crew En Route|Resolved)` |
| `License` | Tenant org subscription | `organizationName, status(active|suspended|expired), geographicBoundaries{type(Polygon|MultiPolygon), coordinates(Mixed GeoJSON)}, subscriptionDetails{planType, startDate, endDate}, assignedSubAdminId→User, billingContact/Address/Email, phoneNumber, radiusMile(default 5)` |
| `Partner` | External nonprofits / businesses | `name, type(nonprofit|business), function, contact, sector, support, area, status(Active|Standby|Inactive)` |
| `PreparednessGuide` | Top-level preparedness category | `category(unique), order` |
| `Ready2GoUserImpacted` | Impacted-citizen ledger | `name, location, city, time, status, subAdminName, lat, lng` |
| `Responder` | First-responder roster | `name, type, status, location, city, availability, contact, coordinates{lat,lng}` |
| `Responder<Vertical>Deployment` (×12) | Per-vertical live deployment posture | See §4.6 |
| `ResponderHospitalCapacity` | Hospital occupancy | `ownerUserId unique, licenseId, facilityId, facilityName, notes, units[{id,name,capacity,occupied,unitType(icu|medsurg|'')}]` |
| `ResponderInvite` | Pending responder invites | `email idx, token unique, responderVertical, responderFunction, licenseId, invitedBy→User, expiresAt, usedAt`; idx `(email, licenseId, usedAt)` |
| `SubAdminTask` | Sub-admin's preparedness tasks | `subAdminId→User, preparednessId→PreparednessGuide, sourceTaskId→Task, title, createdBy(super_admin|sub_admin), isDeletedBySubAdmin, isActive`; collection: `subadmin_tasks` |
| `SystemNotification` | App-wide notifications | `type, subject, message, read, meta(Mixed)`; sparse idx `(type, meta.userId, meta.alertId, meta.channel)` |
| `SystemStatus` | Global app mode | `emergencyMode(safe|danger), updatedAt` |
| `Task` | Super-admin defined task template | `preparednessId→PreparednessGuide idx, title, createdBy(super_admin), createdByUserId→User, isActive`; idx `(preparednessId, isActive)` |
| `UserTask` | User-assigned task instance | `userId→User idx, subAdminId→User, preparednessId→PreparednessGuide, taskId→SubAdminTask, title, description, sentAt`; collection: `user_tasks`; unique idx `(userId, taskId)` |
| `VirtualEOCStatus` | Virtual EOC status log | Same shape as `ActiveEmergency` |
| `WeatherAlertRecord` | NWS-ingested alerts | `alertId unique idx, source, event, severity, title, description, timestamp, expiresAt, weatherType, temperature, windSpeed, humidity, precipitation, coordinates{lat,lon}, affectedAreas[], areaDesc, zones[]`; idx `(source, expiresAt, timestamp desc)`, idx `(event)` |
| `WeatherAlertTypeChangeLog` | Audit of new/removed alert types | `newEvents[], removedEvents[], invalidEnabledEvents[], detectedEvents[], automationPaused, processed` |
| `WeatherAlertTypeConfig` | Per-type alert subscription | `events[{name, enabled, sendPush, sendSms, sendEmail, invalid, lastSeenAt}]` |

### 4.6 — `Responder<Vertical>Deployment` family (12 models)

Each is keyed by `ownerUserId` (unique, indexed) and `licenseId`. Most have a
`sites: [...]` subarray with status enum `'active'|'limited'|'suspended'`,
geocoded address, and personnel/vehicle counts.

| Model | Site subdoc fields | Notes |
|-------|---------------------|-------|
| `ResponderElectricDeployment` | id, name, address, lat, lng, vehiclesDeployed, crewsDeployed, status, notes | `networkId/Name`, `coordinatorNotes`, `source(api|mock)` |
| `ResponderEnergyDeployment` | id, name, address, lat, lng, crewsDeployed, status, notes | |
| `ResponderGasDeployment` | id, name, address, lat, lng, crewsDeployed, status, notes | |
| `ResponderWaterDeployment` | id, name, address, lat, lng, crewsDeployed, status, notes | |
| `ResponderTransitDeployment` | id, name, address, lat, lng, vehiclesDeployed, status, notes | |
| `ResponderFoodLogisticsDeployment` | id, name, address, lat, lng, volunteersDeployed, status, notes | |
| `ResponderNationalGuardDeployment` | id, name, address, lat, lng, personnelDeployed, vehiclesDeployed, status, notes | |
| `ResponderPharmacyDeployment` | id, name, address, lat, lng, status(open|limited|closed), notes | |
| `ResponderNonprofitDeployment` | id, name, address, lat, lng, siteKind(network|shelter|volunteer), volunteersDeployed, shelterCapacity, status, notes | |
| `ResponderFederalDeployment` | id, location, personnelCount, vehicleCount, status(active|standby|demobilized), notes | `stagingAreas[]`, `totalPersonnelDeployed`, `jurisdictionName` |
| `ResponderPoliceDeployment` | — | `agencyId/Name`, `vehiclesDeployed`, `personnelOnDuty`, `incidentOperations[{id,incidentName,teamsDeployed,operationSummary}]`, `stagingAreas[{id,name,address,units}]`, `commanderNotes` |
| `ResponderHospitalCapacity` | — | Listed above |

---

## §5 — API Surface (`app/api/**`)

Total: **95 route files** across 30 top-level folders. All under
`app/api/<path>/route.ts` (App Router).

### 5.1 — Quick-find: routes that handle file uploads or AI calls

| Route | Method | Why it matters |
|-------|--------|----------------|
| `/api/admin/emergency-plans` | `POST` | **[FILE UPLOAD][AI]** — COOP/BC/Compliance file upload + AI integrity + AI metadata inference (Python's primary surface) |
| `/api/admin/emergency-plans/audit-summary` | `POST` | **[AI]** — Regenerates aggregate `ContinuityAudit` |
| `/api/admin/emergency-plans/audit-summary` | `GET` | Returns cached `ContinuityAudit` (no AI call) |
| `/api/admin/emergency-plans/attachment` | `DELETE` | Removes a Cloudinary asset + DB subdoc |
| `/api/upload` | `POST`, `DELETE` | **[FILE UPLOAD]** — generic image upload (jpg/png/webp, ≤10 MB) to Cloudinary, used elsewhere |
| `/api/ai/generate-alert` | `POST` | **[AI]** — ad-hoc alert message generation (not COOP-related) |
| `/api/admin/ai-alert-draft` | `POST` | **[AI]** — drafts an alert blast from incident context |
| `/api/risk-assessment/*` (6 routes) | varies | **[AI]** — AI Risk Assessment pages; reads `UnifiedEvent`, calls OpenAI |
| `/api/alerts/ingest-weather` | `GET` | **[CRON]** — NWS poller meant to be hit every 60 s by an external scheduler |
| `/api/admin/alerts/process-external` | `POST` | Normalizes incoming external alert → `WeatherAlertRecord` |

### 5.2 — Full route inventory (grouped)

#### Auth
- `POST /api/login` — bcrypt-validates user, sets `session`/`userRole`/`accountStatus` cookies, auto-provisions two demo accounts (`public_demo@yopmail.com`, `nonprofit_demo@yopmail.com`)
- `POST /api/logout` — clears cookies
- `POST /api/signup` — creates User (`accountStatus: 'pending'` by default), validates `responderInviteToken` if present, enforces sub-admin uniqueness per `(city, state, country)`
- `POST /api/subadmin/*` — sub-admin preparedness tasks (3 routes)

#### Admin · Emergency Plans (Python's surface)
- `GET /api/admin/emergency-plans` — list all plans + attachments as a `{planId: {...}}` map
- `POST /api/admin/emergency-plans` — upload file (multipart) + run AI integrity → see §6
- `PUT /api/admin/emergency-plans` — create or replace plan metadata (no file)
- `PATCH /api/admin/emergency-plans` — patch label / overview / category on existing plan
- `POST /api/admin/emergency-plans/steps` — save the array of plan steps
- `DELETE /api/admin/emergency-plans/attachment` — remove one attachment (Cloudinary + DB)
- `GET /api/admin/emergency-plans/audit-summary` — cached `ContinuityAudit`
- `POST /api/admin/emergency-plans/audit-summary` — regenerate + persist

All admin/emergency-plans routes require `role ∈ {super-admin, sub-admin, admin}`.

#### Admin · Other
- `/api/admin/after-action-review` — AAR CRUD
- `/api/admin/alert-types` — config of subscribable NWS alert types
- `/api/admin/alerts/process-external` — accepts external alert payload, normalizes
- `/api/admin/broadcast-country-alert` — country-scoped broadcast
- `/api/admin/citizens/locations` — citizen location feed
- `/api/admin/dispatch-settings` — read/write `DispatchSettings`
- `/api/admin/eoc/`, `/api/admin/eoc-settings/`, `/api/admin/eoc-setup-status/`
- `/api/admin/licenses` — `License` CRUD
- `/api/admin/national-alert-dispatch`
- `/api/admin/partners` — `Partner` CRUD
- `/api/admin/personnel`
- `/api/admin/preparedness-guides`, `/api/admin/preparedness-tasks` (+ `/send`, `/[taskId]`)
- `/api/admin/responder-invites`
- `/api/admin/stats` — admin dashboard counters
- `/api/admin/sub-admin-country-status`
- `/api/admin/tasks`
- `/api/admin/users`, `/api/admin/users/search`, `/api/admin/users/request-license`
- `/api/admin/virtual-eoc`
- `/api/admin/ai-alert-draft` — **[AI]** drafts alert from incident context

#### AI
- `POST /api/ai/generate-alert` — **[AI]** generic alert-message generator

#### Alerts & Comms
- `GET /api/alerts/all` — aggregated feed
- `GET /api/alerts-communication` — composite alerts/messaging feed
- `GET /api/alerts/community` — `CommunityAlert` CRUD
- `GET /api/alerts/earthquake` — USGS earthquakes
- `GET /api/alerts/ingest-weather` — **[CRON]** NWS poller (see `doc/one-minute-polling.md`)
- `GET /api/alerts/users` — user-personalized alerts
- `GET /api/alerts/weather` — current NWS weather alerts
- `GET /api/alerts-sent-emergencies` — `AlertSentEmergency` log

#### Events / Incidents / Threats
- `GET /api/events`
- `GET /api/active-emergencies`
- `GET /api/incidents`
- `GET /api/threats/assessment` — **[AI]** threat-assessment summary
- `GET /api/unified-events/historical-sync` — backfill historical UnifiedEvents

#### Risk Assessment (AI Risk page — out of scope, but uses OpenAI)
- `POST /api/risk-assessment/analyze`
- `GET /api/risk-assessment/historical/[category]`
- `POST /api/risk-assessment/incident-details`
- `GET /api/risk-assessment/severity-summaries`
- `POST /api/risk-assessment/strategic-plan`
- `GET /api/risk-assessment/summary`

#### Preparedness
- `GET /api/preparedness-guides`
- `GET|POST /api/preparedness-tasks`, `/[id]`, `/send`
- `GET /api/preparedness-with-tasks` — composite (drives main page)

#### Field / EOC / Setup
- `GET|POST /api/field-reports` — `FieldReport`
- `GET /api/virtual-eoc-status`
- `GET /api/setup-wizard/[licenseId]` — License-setup wizard

#### Responder (read-heavy, vertical-specific)
- `/api/responder/dashboard`
- `/api/responder/<vertical>/resource-deployment` (×11: electric, energy, federal, food-logistics, gas, hospital/capacity, hotel/availability, national-guard, nonprofit, pharmacy, transit, water)
- `/api/responder/police/deployment`
- `/api/responder/public-official`
- `/api/responder-invite/preview`
- `/api/responders`

#### Geo
- `GET /api/geocode` — forward geocode
- `GET /api/reverse-geocode` — reverse geocode
- `GET /api/places` — Places autocomplete

#### Uploads
- `POST /api/upload` — generic image upload (jpg/png/webp, 10 MB) to Cloudinary
- `DELETE /api/upload` — delete by `public_id`

#### User-scoped
- `GET /api/user/activity-log` — read `ActivityLog`
- `POST /api/user/change-password`
- `GET /api/user/emergency-plan` — user's personal plan view
- `GET|POST|DELETE /api/user/favorite-places`, `/[id]`
- `GET|PATCH /api/user/notification-preferences`
- `GET|PATCH /api/user/profile`
- `POST /api/user/safety` — user safety check-in
- `PATCH /api/user/security`
- `GET /api/user/tasks` — `UserTask` list
- `POST /api/user/update-location`
- `GET /api/ready2go-users-impacted` — `Ready2GoUserImpacted` feed

### 5.3 — Verbatim: upload route (`app/api/admin/emergency-plans/route.ts`)

The `POST` handler is the core integration point (truncated to the relevant
section — full file is 377 lines):

```ts
const MAX_BYTES = 25 * 1024 * 1024;
const ALLOWED_EXT = new Set(['pdf', 'docx', 'csv', 'xlsx']);

export async function POST(req: Request) {
  const session = await getSession();
  if (!session?.user?.role || !canManageEmergencyPlans(session.user.role)) {
    return NextResponse.json({ success: false, error: 'Unauthorized' }, { status: 401 });
  }
  await connectDB();

  const formData = await req.formData();
  const file = formData.get('file');
  if (!file || !(file instanceof File))
    return NextResponse.json({ success: false, error: 'Missing file' }, { status: 400 });

  const ext = extensionFromFilename(file.name);
  if (!ALLOWED_EXT.has(ext))
    return NextResponse.json({ success: false, error: `Unsupported file type ".${ext || '?'}"` }, { status: 415 });
  if (file.size <= 0) return NextResponse.json({ success: false, error: 'Empty file' }, { status: 400 });
  if (file.size > MAX_BYTES) return NextResponse.json({ success: false, error: 'File too large …' }, { status: 413 });

  const buffer = Buffer.from(await file.arrayBuffer());
  const mime = file.type?.trim() || mimeFromExtension(ext);

  // 1) Cloudinary upload
  const upload = await uploadEmergencyPlanBuffer({ buffer, mime, filename: file.name.replace(/[^\w.-]+/g, '_') });

  // 2) Extract text once (used by metadata + integrity)
  let extractedText = '';
  try { extractedText = await extractTextFromBuffer(buffer, ext, { maxChars: FAST_TEXT_CAP, maxSheets: 5 }); }
  catch (e) { console.error('EmergencyPlan upload text extract:', e); }

  // 3) AI metadata inference (planId / label / category / overview)
  let metadata;
  try {
    metadata = await openaiService.inferCoopPlanMetadata({
      fileName: file.name, fileExtension: ext, fileSizeBytes: buffer.length,
      extractedText: extractedText || undefined,
    });
  } catch (e) { console.error('EmergencyPlan upload metadata inference:', e); metadata = null; }

  const resolvedPlanId  = metadata?.planId  || file.name.replace(/\.[^.]+$/, '').toLowerCase().replace(/[^a-z0-9-_]+/g, '-')…;
  const resolvedLabel   = metadata?.label   || file.name.replace(/\.[^.]+$/, '');
  const resolvedCategory= metadata?.category|| 'coop';
  const resolvedOverview= metadata?.overview|| `Imported ${ext.toUpperCase()} continuity artifact pending review.`;

  // 4) Find-or-create plan, then push attachment
  let plan = await EmergencyPlan.findOne({ planId: resolvedPlanId });
  const planExisted = Boolean(plan);
  if (!plan) plan = new EmergencyPlan({ planId: resolvedPlanId, label: resolvedLabel, overview: resolvedOverview, category: resolvedCategory, steps: [], attachments: [] });

  plan.attachments.push({
    fileName: file.name,
    fileUrl: upload.secure_url,
    size: buffer.length,
    uploadedAt: new Date(),
    cloudinaryPublicId: upload.public_id,
    cloudinaryResourceType: upload.resource_type,
  });
  await plan.save();

  // 5) AI Integrity analysis on the freshly-pushed attachment
  const lastAtt = plan.attachments[plan.attachments.length - 1];
  const attachmentId = lastAtt?._id;
  if (attachmentId) {
    try {
      const result = await openaiService.analyzeCoopAttachmentIntegrity({
        planLabel: plan.label, planOverview: plan.overview || '',
        steps: Array.isArray(plan.steps) ? plan.steps.map(String) : [],
        fileName: file.name, fileExtension: ext, fileSizeBytes: buffer.length,
        extractedText: extractedText || undefined,
        maxExcerptChars: FAST_TEXT_CAP,
      });
      await EmergencyPlan.updateOne(
        { planId: resolvedPlanId, 'attachments._id': attachmentId },
        { $set: {
            'attachments.$.aiIntegrityStatus':   result.status,
            'attachments.$.aiIntegrityScore':    result.score,
            'attachments.$.aiIntegritySummary':  result.summary,
            'attachments.$.aiIntegrityAnalyzedAt': new Date(),
        }},
      );
    } catch (e) { console.error('EmergencyPlan upload AI integrity:', e); }
  }

  return NextResponse.json({ success: true, attachedToExistingPlan: planExisted, planId: resolvedPlanId, data: refreshed });
}
```

Auth check helper (note who is authorized):

```ts
function canManageEmergencyPlans(role: string | undefined) {
  return role === 'super-admin' || role === 'sub-admin' || role === 'admin';
}
```

### 5.4 — Verbatim: audit-summary route

```ts
async function buildAuditInput(): Promise<ContinuityAuditInput> {
  const plans = await EmergencyPlan.find({}).lean<…>();
  const counts = { coop: 0, bcp: 0, compliance: 0, response: 0 };
  const integrity = { inSync: 0, reviewing: 0, deviation: 0, unanalyzed: 0 };
  let scoreSum = 0, scoreCount = 0, totalAttachments = 0, analyzed = 0;

  const planSummaries = plans.map((p) => {
    const cat = resolveCategory(p.category, p.planId);   // coop|bcp|compliance|response
    const atts = p.attachments || [];
    counts[cat] += atts.length;
    totalAttachments += atts.length;

    const attachmentSummaries = atts.map((a) => {
      if (a.aiIntegrityStatus === 'In Sync')          integrity.inSync++;
      else if (a.aiIntegrityStatus === 'Deviation Found') integrity.deviation++;
      else if (a.aiIntegrityStatus === 'Reviewing')   integrity.reviewing++;
      else                                            integrity.unanalyzed++;
      if (typeof a.aiIntegrityScore === 'number') { scoreSum += a.aiIntegrityScore; scoreCount++; }
      if (a.aiIntegrityAnalyzedAt) analyzed++;
      return { fileName: a.fileName, status: a.aiIntegrityStatus, score: a.aiIntegrityScore, summary: a.aiIntegritySummary };
    });

    return { planId: p.planId, label: p.label, category: cat, attachmentCount: atts.length, stepCount: (p.steps||[]).length, attachments: attachmentSummaries };
  });

  return {
    totals: { plans: plans.length, attachments: totalAttachments, analyzed },
    averageScore: scoreCount ? Math.round(scoreSum / scoreCount) : 0,
    counts, integrity, plans: planSummaries,
  };
}

export async function POST() {
  // … auth …
  await connectDB();
  const input  = await buildAuditInput();
  const result = await openaiService.generateContinuityAuditSummary(input);
  await ContinuityAudit.findOneAndUpdate(
    { scope: 'global' },
    { $set: { scope:'global', summary: result.summary, findings: result.findings, posture: result.posture,
              averageScore: result.averageScore, totals: input.totals, integrity: input.integrity, generatedAt: new Date() } },
    { upsert: true, new: true },
  );
  return NextResponse.json({ success: true, data: { …result, totals: input.totals, integrity: input.integrity, generatedAt: now } });
}
```

---

## §6 — AI Integrity & Summary Pipeline (CURRENT STATE) — **HEADLINE**

This is the section you came here for: exactly what the Next.js app does today,
so you know what to replace.

### 6.1 — Single AI hub

All AI calls live in **`lib/services/openai-service.ts`** (~1 610 lines). One
class, `OpenAIService`, instantiated as `openaiService`. No SDK — uses raw
`fetch()`.

```ts
private apiKey = process.env.OPENAI_API_KEY || '';
private model  = process.env.OPENAI_MODEL    || 'gpt-4o-mini';

private async callOpenAI<T>(messages, fallback: T, options?: {max_tokens?, model?, temperature?}): Promise<T> {
  if (!this.canUseOpenAI()) return fallback;
  const effectiveModel = options?.model || this.model;
  try {
    const response = await fetch('https://api.openai.com/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${this.apiKey}` },
      body: JSON.stringify({
        model: effectiveModel,
        messages,
        response_format: { type: 'json_object' },
        ...(options?.max_tokens ? { max_tokens: options.max_tokens } : {}),
        ...(options?.temperature !== undefined ? { temperature: options.temperature } : {}),
      }),
    });
    if (!response.ok) throw new Error(`OpenAI request failed: ${response.status}`);
    const data = await response.json();
    const content = data?.choices?.[0]?.message?.content;
    if (!content) return fallback;
    return JSON.parse(content) as T;
  } catch (error) { return fallback; }
}
```

**Notable** — every call uses `response_format: { type: 'json_object' }` and
expects the response to be valid JSON parsed into `T`. Failures fall back
silently to the supplied fallback.

### 6.2 — Step-by-step flow on file upload

```
┌─────────────────────────┐
│ User clicks "ADD PLANS" │
└───────────┬─────────────┘
            │ multipart/form-data, field "file"
            ▼
┌────────────────────────────────────────────────────────────┐
│ POST /api/admin/emergency-plans                            │
│ (auth: super-admin | sub-admin | admin)                    │
├────────────────────────────────────────────────────────────┤
│ 1. Validate ext ∈ {pdf,docx,csv,xlsx}, size ≤ 25 MB        │
│ 2. uploadEmergencyPlanBuffer()  → Cloudinary               │
│ 3. extractTextFromBuffer(maxChars = FAST_TEXT_CAP = 8000)  │
│ 4. openaiService.inferCoopPlanMetadata(...)                │
│      ⮕ {planId, label, category, overview}                 │
│ 5. EmergencyPlan.findOne({planId}) or new …                │
│      plan.attachments.push({ fileName, fileUrl,            │
│        size, uploadedAt, cloudinaryPublicId, …Type })      │
│      plan.save()                                            │
│ 6. openaiService.analyzeCoopAttachmentIntegrity(...)       │
│      ⮕ {status, score, summary}                            │
│ 7. EmergencyPlan.updateOne({planId, attachments._id},      │
│      $set: {                                                │
│        'attachments.$.aiIntegrityStatus':     status,      │
│        'attachments.$.aiIntegrityScore':      score,       │
│        'attachments.$.aiIntegritySummary':    summary,     │
│        'attachments.$.aiIntegrityAnalyzedAt': new Date()   │
│      })                                                     │
└────────────────────────────────────────────────────────────┘
```

### 6.3 — Verbatim: `inferCoopPlanMetadata`

```ts
async inferCoopPlanMetadata(input: { fileName, fileExtension, fileSizeBytes, extractedText? }):
  Promise<CoopPlanMetadata>
{
  const ext = String(input.fileExtension || '').toLowerCase().replace(/^\./, '');
  const excerpt = (input.extractedText || '').trim().slice(0, 8000);
  const fallback = this.coopMetadataFallback(input.fileName, ext);
  if (!this.canUseOpenAI()) return fallback;

  const payload = {
    fileName: input.fileName, fileExtension: ext, fileSizeBytes: input.fileSizeBytes,
    fileKind: ext === 'pdf' ? 'PDF' : ext==='docx'||ext==='doc' ? 'Word'
            : ext==='xlsx'||ext==='xls' ? 'Spreadsheet'
            : ext==='csv' ? 'CSV' : 'Document',
    documentTextExcerpt: excerpt.length ? excerpt : null,
  };

  const system = `You are a Continuity-of-Operations records librarian for the Ready2Go platform.
Given a single uploaded file (continuity-plan artifact), return STRICT JSON only:
{"planId":"<slug>","label":"<short title>","category":"coop"|"bcp"|"compliance","overview":"<1-2 sentences, max 280 chars>"}

Rules:
- planId: lowercase ASCII slug, 3-60 chars, [a-z0-9-] only, no leading/trailing hyphen.
  Derive it from the document's *subject* (e.g. "pandemic-coop-plan", "it-disaster-recovery",
  "annual-compliance-training-register"). If two different uploads describe the same program,
  the slug MUST collide (so they get attached to the same plan).
- label: human-readable title in Title Case, max 80 chars.
- category:
   * "coop"        — Continuity of Operations: essential functions, succession, vital records,
                     hazard response playbooks for the *organization*, pandemic, evacuation,
                     devolution, reconstitution.
   * "bcp"         — Business Continuity: IT/telecom disaster recovery, network, supply chain,
                     vendor failover, facilities, RTO/RPO.
   * "compliance"  — Regulatory & audit: training records, NIMS/ICS, HIPAA, OSHA, audit findings,
                     attestations, retention/policy.
- overview: factual purpose statement grounded in the excerpt (no marketing language, no markdown).
  If excerpt is empty, infer from the file name conservatively and keep overview short.
- Never output keys other than planId / label / category / overview.`;

  const raw = await this.callOpenAI<Partial<CoopPlanMetadata>>(
    [{ role: 'system', content: system }, { role: 'user', content: JSON.stringify(payload) }],
    fallback, { max_tokens: 220 },
  );
  return { planId: normalizePlanSlug(raw.planId, fallback.planId),
           label:  normalizeLabel(raw.label, fallback.label),
           overview: normalizeOverview(raw.overview, fallback.overview),
           category: normalizePlanCategory(raw.category, fallback.category) };
}
```

The fallback (when `OPENAI_API_KEY` missing) just slugs the filename and
defaults `category` to `'coop'`.

### 6.4 — Verbatim: `analyzeCoopAttachmentIntegrity` (THE prompt)

```ts
async analyzeCoopAttachmentIntegrity(input: {
  planLabel, planOverview, steps, fileName, fileExtension, fileSizeBytes,
  extractedText?, maxExcerptChars?
}): Promise<CoopAttachmentIntegrity> {

  const fallback: CoopAttachmentIntegrity = {
    status: 'Reviewing', score: 50,
    summary: 'Analysis unavailable — configure OPENAI_API_KEY or retry.',
  };

  const cap = input.maxExcerptChars && input.maxExcerptChars > 0 ? input.maxExcerptChars : 12000;
  const ext = String(input.fileExtension || '').toLowerCase().replace(/^\./, '');
  const excerpt = (input.extractedText || '').trim().slice(0, cap);

  const payload = {
    fileKind: ext === 'pdf' ? 'PDF' : ext==='docx'||ext==='doc' ? 'Word'
            : ext==='xlsx'||ext==='xls' ? 'Spreadsheet'
            : ext==='csv' ? 'CSV' : 'Document',
    planLabel: input.planLabel,
    planOverview: input.planOverview.slice(0, 4000),
    planSteps: input.steps.slice(0, 50).join('\n').slice(0, 6000),
    fileName: input.fileName, fileExtension: ext, fileSizeBytes: input.fileSizeBytes,
    documentTextExcerpt: excerpt.length ? excerpt : null,
    guidance: excerpt.length === 0
      ? 'No extractable text (empty, corrupted, or scan-only PDF). Score conservatively (35–55) and use Reviewing or Deviation Found as appropriate; explain in summary.'
      : `Evaluate the excerpt as a ${ext.toUpperCase()} continuity / emergency-plan artifact. Check alignment with plan overview and steps; clarity of objectives, roles, communications, recovery threads; tabular data coherence for spreadsheets.`,
  };

  const system = `You are a continuity of operations (COOP) and emergency preparedness document reviewer for Ready2Go.
The uploaded files are limited to PDF, DOCX, CSV, and XLSX for this module.
Return ONLY valid JSON: {"status":"In Sync"|"Reviewing"|"Deviation Found","score":<integer 0-100>,"summary":"<plain English, max 220 chars>"}.
Status meanings:
- "In Sync": content substantively supports the plan context and looks operationally usable.
- "Reviewing": partial/unclear content, weak alignment, or needs human review.
- "Deviation Found": serious gaps, wrong intent vs plan, or unusable as a continuity artifact.
Score: 0–100 (higher = stronger alignment and completeness). Map score visually:
  low scores (~0–40) imply deviation risk; mid (41–70) review; high (71–100) in sync.
Do not give legal advice. If excerpt is empty or useless, keep score low and status Reviewing or Deviation Found.`;

  const raw = await this.callOpenAI<{ status?, score?, summary? }>(
    [{ role: 'system', content: system }, { role: 'user', content: JSON.stringify(payload) }],
    fallback, { max_tokens: 380 },
  );

  return {
    status:  normalizeCoopIntegrityStatus(raw.status),
    score:   Math.min(100, Math.max(0, Math.round(Number(raw.score) || fallback.score))),
    summary: String(raw.summary || fallback.summary).slice(0, 280),
  };
}
```

Status normalization (case-insensitive, defaults to `'Reviewing'`):

```ts
private normalizeCoopIntegrityStatus(s: string | undefined): 'In Sync'|'Reviewing'|'Deviation Found' {
  const u = String(s || '').trim().toLowerCase();
  if (u === 'deviation found' || (u.includes('deviation') && !u.includes('no deviation'))) return 'Deviation Found';
  if (u === 'in sync' || u.includes('in sync')) return 'In Sync';
  if (u === 'reviewing' || u.includes('review')) return 'Reviewing';
  return 'Reviewing';
}
```

### 6.5 — Verbatim: `generateContinuityAuditSummary` + `derivePosture`

```ts
async generateContinuityAuditSummary(input: ContinuityAuditInput): Promise<ContinuityAuditSummary> {
  const posture = this.derivePosture(input);
  const fallback: ContinuityAuditSummary = {
    summary: input.totals.plans
      ? `Continuity vault holds ${input.totals.plans} plan${…} and ${input.totals.attachments} attachment${…}; configure OPENAI_API_KEY for a tailored audit.`
      : 'No continuity plans yet — register a plan to begin tracking COOP/BCP/Compliance posture.',
    findings: [], posture, averageScore: input.averageScore,
  };
  if (!this.canUseOpenAI() || !input.totals.plans) return fallback;

  const compactPlans = input.plans.slice(0, 25).map((p) => ({
    planId: p.planId, label: p.label.slice(0, 80), category: p.category,
    files: p.attachmentCount, steps: p.stepCount,
    attachments: p.attachments.slice(0, 6).map((a) => ({
      file: a.fileName.slice(0, 80), status: a.status || 'unscored',
      score: typeof a.score === 'number' ? a.score : null,
    })),
  }));

  const payload = { totals: input.totals, averageScore: input.averageScore,
                    categoryCounts: input.counts, integrityBreakdown: input.integrity,
                    plans: compactPlans };

  const system = `You are a Continuity-of-Operations auditor for the Ready2Go emergency-management platform.
Given a JSON inventory of COOP/BCP/Compliance plans and their AI-integrity scored attachments, output ONLY JSON:
{"summary":"<one or two sentences, plain English, max 360 chars, no markdown>","findings":["<short actionable bullet, max 140 chars>", ...]}
Rules:
- 2 to 4 findings, ordered by urgency. Reference real plan labels or categories where useful.
- Highlight: coverage gaps (empty categories), low integrity scores, files with "Deviation Found",
  plans without steps or attachments, missing analysis.
- If posture is strong, still flag the weakest area for continuous improvement.
- Do NOT recommend actions outside the continuity/emergency-management domain. No legal advice.`;

  const raw = await this.callOpenAI<{ summary?, findings? }>(
    [{ role: 'system', content: system }, { role: 'user', content: JSON.stringify(payload) }],
    { summary: fallback.summary, findings: [] }, { max_tokens: 420 });

  const findings = Array.isArray(raw.findings)
    ? raw.findings.map((f) => String(f ?? '').trim()).filter(Boolean).slice(0, 4).map((f) => f.slice(0, 160))
    : [];
  return { summary: String(raw.summary || fallback.summary).slice(0, 420), findings, posture, averageScore: input.averageScore };
}

private derivePosture(input: ContinuityAuditInput): 'Resilient'|'Steady'|'At Risk' {
  if (!input.totals.plans) return 'At Risk';
  const deviations = input.integrity.deviation;
  const reviewing  = input.integrity.reviewing;
  const analyzed   = input.totals.analyzed;
  const avg        = input.averageScore;
  if (deviations > 0 || avg < 55 || analyzed === 0) return 'At Risk';
  if (reviewing > 0 || avg < 75)                    return 'Steady';
  return 'Resilient';
}
```

### 6.6 — MongoDB write-back contract (cheat sheet)

**Per-file** (after every successful integrity run):

| Collection | Document selector | Field path | Type | Constraint |
|------------|------------------|------------|------|------------|
| `emergencyplans` | `{ planId, 'attachments._id': attachmentId }` | `attachments.$.aiIntegrityStatus` | String | one of: `"In Sync"`, `"Reviewing"`, `"Deviation Found"` |
| `emergencyplans` | same | `attachments.$.aiIntegrityScore` | Number | integer 0..100 |
| `emergencyplans` | same | `attachments.$.aiIntegritySummary` | String | ≤ 280 chars, plain text |
| `emergencyplans` | same | `attachments.$.aiIntegrityAnalyzedAt` | Date | ISO timestamp |

**Aggregate** (after every full audit run):

Upsert into `continuityaudits` with `{ scope: 'global' }`. Set the entire
document (summary, findings, posture, averageScore, totals, integrity,
generatedAt).

### 6.7 — Inputs the AI service needs (to reproduce parity)

From the upload route:
- `planLabel` (string)
- `planOverview` (string, trimmed at 4 000 chars before prompt)
- `planSteps` (string[], first 50 joined and trimmed at 6 000 chars)
- `fileName`, `fileExtension`, `fileSizeBytes`
- `documentTextExcerpt` (first 8 000 chars — `FAST_TEXT_CAP` on upload, can be
  12 000 on manual rescan)

From the audit-summary route:
- For each plan: `planId`, `label`, `category`, `attachmentCount`, `stepCount`
- For each attachment (up to 6 per plan, up to 25 plans): `fileName`, `status`, `score`
- Aggregate: `totals.{plans, attachments, analyzed}`, `averageScore`, `categoryCounts`, `integrityBreakdown`

### 6.8 — Status / cost / weaknesses (the user's stated motivation)

- **One OpenAI call per upload** (~8 k input + ~80 output tokens) + **one per
  audit refresh**.
- **No caching** — re-uploading the same file re-runs the LLM.
- **No retries** — `callOpenAI` swallows errors and returns the fallback.
- **No chunking** — only the first 8 000 chars of a document are scored.
- **Silent extraction failure** — if `pdf-parse` / `mammoth` / `xlsx` throws,
  `extractTextFromBuffer` returns `''`; the AI is still called with an empty
  excerpt and follows the "no extractable text" guidance to score 35–55.
- **Magic thresholds**: `avg < 55 → At Risk`, `avg < 75 → Steady`,
  `avg ≥ 75 + 0 reviewing + 0 deviation → Resilient`. Not calibrated.
- **Naive regex** for response-vs-coop-vs-bcp inference (see `inferCategory()`
  in `audit-summary/route.ts:14–25` and `inferCategoryFromPlanId()` in
  `app/(admin)/emergency-plan/page.tsx:90–102`).
- **No vector store** at all — each integrity check is a fresh LLM call with
  the whole plan context inlined.
- **No structured logging** of AI calls — just `console.error` on failure.

---

## §6.5 — Adjacent AI / Scoring Code in This Repo

So you don't accidentally duplicate or break things, here's what else
`lib/services/openai-service.ts` and friends do (all out-of-scope for the
integrity service, but worth knowing):

| Function in `openai-service.ts` | Used by | Stays in Next.js? |
|-------|-------|-------|
| `analyzeThreatAssessment()` | `/api/threats/assessment` | Yes |
| `detectOperationalSignals()` | dashboard intelligence | Yes |
| `generateEmergencyInsights()` | dashboard | Yes |
| `generateDynamicNews()`, `generateSocialMediaPosts()` | dashboard demo data | Yes |
| `generateResourceReports()`, `generateLodgingReports()` | responder dashboards | Yes |
| `generatePreparednessTips()` | preparedness pages | Yes |
| `generateAfterActionInsights()` | `/after-action-review` | Yes |
| `generateAlertMessage()` | `/api/ai/generate-alert`, `/api/admin/ai-alert-draft` | Yes |
| `synthesizeRiskReport()` (~700 lines) | `/api/risk-assessment/*` | Yes |
| `generateHistoricalContext()` etc. | risk-assessment historical tabs | Yes |
| **`inferCoopPlanMetadata`** | upload route | **REPLACE** |
| **`analyzeCoopAttachmentIntegrity`** | upload route | **REPLACE** |
| **`generateContinuityAuditSummary`** | audit endpoint | **REPLACE** |

The Risk Assessment AI uses a shared `PLAIN_ENGLISH_STYLE_RULES` constant
(`lib/services/openai-service.ts:137`) for all public-facing reports. Keep this
voice if your service ever generates user-facing copy.

### Other `lib/services/` modules

| File | Purpose |
|------|---------|
| `risk-ingest-service.ts` | Aggregates NWS / USGS / NWPS / FIRMS / FEMA / InciWeb / WFIGS for the AI risk report (on-demand) |
| `unified-event-repo.ts`, `unified-event-historical-ingest.ts` | UnifiedEvent DB operations + backfill |
| `flood-service.ts`, `earthquake-api.ts`, `wildfire-service.ts`, `weather-api.ts`, `usgs-earthquake-fdsnws.ts`, `openfema-service.ts` | External-feed adapters |
| `alert-communication-*-sync.ts` (3 files) | NWS-driven alert-comms feed |
| `notification-service.ts` | Multi-channel notify (push/sms/email) |
| `event-formatters.ts`, `event-grouping.ts` | UI-friendly UnifiedEvent transforms |
| `risk-kpi-dynamic.ts`, `risk-event-distribution.ts`, `risk-ai-confidence.ts`, `risk-current-snapshot.ts`, `risk-historical-context.ts`, `risk-report-alert-alignment.ts`, `risk-responder-data.ts`, `risk-similar-events.ts`, `risk-exposure-service.ts`, `risk-ingest-state-scope.ts` | Risk-report assembly helpers |
| `location-matching.ts`, `nwps-reach-mapper.ts`, `census-county-population.ts`, `places-service.ts` | Geo / location helpers |
| `mock-map-service.ts`, `mock-weather-service.ts`, `social-media-api.ts`, `hotel-api-service.ts`, `gas-buddy-service.ts`, `ready2go-reachable-users.ts` | Mocks and adapters |
| `alert-processor.ts` | Generic alert ingest |

### Other `lib/` modules to be aware of

| File | Purpose |
|------|---------|
| `lib/mongodb.ts` | Mongoose connection (cached global) — see §4 |
| `lib/auth.ts` | jose JWT encrypt/decrypt + `getSession()` cookie reader |
| `lib/cloudinary.ts` | Lazy Cloudinary config (`requiredEnv()` throws if missing) |
| `lib/emergency-plan-cloudinary.ts` | Buffer → Cloudinary upload, asset destroy, signed-URL helper |
| `lib/emergency-plan-ai-integrity.ts` | Text extraction (`extractTextFromBuffer`, `FAST_TEXT_CAP`, `DEFAULT_TEXT_CAP`) |
| `lib/activity-log.ts`, `lib/activity-actions.ts` | `recordActivity()` writer + `ACTIVITY_ACTIONS` enum |
| `lib/responder-verticals.ts` | `RESPONDER_VERTICALS` canonical list — see §12 |
| `lib/normalization/` | External-feed → `UnifiedEvent` mappers |
| `lib/unified-event/` | UnifiedEvent domain helpers |
| `lib/risk-assessment/` | AI risk-assessment data prep |
| `lib/scoring/` | Deterministic (non-LLM) scoring helpers |
| `lib/notification-preferences/` | Per-user channel toggles |
| `lib/preparedness-tasks/`, `lib/admin-filters.ts`, `lib/api-service.ts` | Misc page helpers |
| `lib/types/` | Shared TypeScript types |

**Audit-trail recommendation**: every write the Python service performs should
also append to `ActivityLog` via the same shape `recordActivity()` produces, so
the existing user-activity UI keeps working. Action constants live in
`lib/activity-actions.ts`.

---

## §7 — File Storage & Extraction

### Cloudinary

```ts
// lib/cloudinary.ts
function getCloudinary() {
  if (!configured) {
    cloudinary.config({
      cloud_name: requiredEnv('CLOUDINARY_CLOUD_NAME'),
      api_key:    requiredEnv('CLOUDINARY_API_KEY'),
      api_secret: requiredEnv('CLOUDINARY_API_SECRET'),
      secure: true,
    });
    configured = true;
  }
  return cloudinary;
}
```

```ts
// lib/emergency-plan-cloudinary.ts
export async function uploadEmergencyPlanBuffer(params: { buffer, mime, filename, folder? })
  : Promise<{ secure_url, public_id, resource_type: 'image'|'raw' }>
{
  const resource_type = mimeToCloudinaryResourceType(params.mime || 'application/octet-stream');
  // image/* → 'image', everything else → 'raw'
  const cld = getCloudinary();
  return new Promise((resolve, reject) => {
    const stream = cld.uploader.upload_stream(
      {
        resource_type,
        folder: params.folder || 'earthquick/emergency-plans',
        filename_override: params.filename,
        use_filename: true,
        unique_filename: true,
        access_mode: 'public',
      },
      (err, result) => {
        if (err) return reject(err);
        if (!result?.secure_url || !result?.public_id) return reject(new Error('Cloudinary returned no upload result'));
        resolve({ secure_url: result.secure_url, public_id: result.public_id, resource_type });
      },
    );
    stream.end(params.buffer);
  });
}
```

A `getSignedDeliveryUrl()` helper exists for authenticated assets, in case
folder ACLs change.

### Text extraction (verbatim — `lib/emergency-plan-ai-integrity.ts`)

```ts
export const FAST_TEXT_CAP    = 8000;    // upload-time
export const DEFAULT_TEXT_CAP = 12000;   // manual rescan

export async function extractTextFromBuffer(buffer: Buffer, ext: string, opts?: { maxChars?, maxSheets? }): Promise<string> {
  const maxChars  = opts?.maxChars  ?? DEFAULT_TEXT_CAP;
  const maxSheets = opts?.maxSheets ?? 15;
  const e = ext.replace(/^\./, '').toLowerCase();

  if (e === 'csv') return buffer.toString('utf8').slice(0, maxChars);

  if (e === 'pdf') {
    try {
      const { PDFParse } = await import('pdf-parse');
      const parser = new PDFParse({ data: buffer });
      const result = await parser.getText();
      await parser.destroy();
      return String(result?.text || '').slice(0, maxChars);
    } catch { return ''; }
  }

  if (e === 'docx') {
    try {
      const mammoth = await import('mammoth');
      const { value } = await mammoth.extractRawText({ buffer });
      return String(value || '').slice(0, maxChars);
    } catch { return ''; }
  }

  if (e === 'xlsx' || e === 'xls') {
    try {
      const wb = XLSX.read(buffer, { type: 'buffer', cellDates: true });
      const chunks: string[] = [];
      for (const name of wb.SheetNames.slice(0, maxSheets)) {
        const sheet = wb.Sheets[name];
        if (!sheet) continue;
        chunks.push(`--- Sheet: ${name} ---`);
        chunks.push(XLSX.utils.sheet_to_csv(sheet));
      }
      return chunks.join('\n').slice(0, maxChars);
    } catch { return ''; }
  }

  return '';
}
```

### Where extracted text ends up

**Nowhere persistent.** The text is built in memory inside the upload handler,
shipped to OpenAI, then discarded. If you want vector embeddings to survive,
the Python service must persist them on its side (vector DB), keyed by
`attachment._id` (or a content hash — see §11.5).

### Upload constraints

- COOP module — `/api/admin/emergency-plans` `POST`:
  - Extensions: `pdf`, `docx`, `csv`, `xlsx`
  - Max size: **25 MB** (`MAX_BYTES = 25 * 1024 * 1024`)
- Generic `/api/upload` `POST`:
  - MIME: `image/jpeg`, `image/png`, `image/webp`
  - Max size: 10 MB

---

## §8 — Background Workers, Realtime, External Feeds

### Status callout

- The `server/workers/` directory **does not exist** even though
  `package.json` declares `worker:ingest` and `worker:stream`. They are
  placeholders.
- `ioredis` is in `package.json` but **not imported anywhere** in source.
- `socket.io` / `socket.io-client` are in `package.json` but **not used**.
- No persistent message broker. No long-running consumers.

### Cron-style entry point

`GET /api/alerts/ingest-weather` is the only feed designed to be polled. An
external scheduler (Vercel Cron, GitHub Actions, etc.) hits it every ~60 s.
See `doc/one-minute-polling.md`:

> **Entry point**: `GET /api/alerts/ingest-weather`
> **Behavior**: geocode user locations, fetch NWS alerts per point, upsert to
> `WeatherAlertRecord`.
> **Safety**: detect new alert types, disable feed, notify admins.
> **Cron**: external scheduler calls the endpoint every 60 seconds.

### External data feeds (all on-demand)

Every feed adapter lives in `lib/services/` and is called synchronously by API
routes (typically as part of building the AI risk report from
`risk-ingest-service.ts`).

| Feed | Base URL | Auth | Code | Env |
|------|----------|------|------|-----|
| NWS Active Alerts | `https://api.weather.gov/alerts/active` | UA only | `lib/services/weather-api.ts`, `…/alert-communication-nws-sync.ts` | `NWS_*` |
| USGS Earthquakes | `https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson` | none | `lib/services/earthquake-api.ts`, `usgs-earthquake-fdsnws.ts` | `USGS_*`, `RISK_EARTHQUAKE_RADIUS_KM` |
| USGS NWIS hydrology | `https://waterservices.usgs.gov/nwis` | none | inside risk-ingest | `USGS_SITES`, `RISK_USGS_SITES` |
| NOAA NWPS water-prediction | `https://api.water.noaa.gov/nwps/v1` | UA | `lib/services/flood-service.ts`, `nwps-reach-mapper.ts` | `NWPS_*`, `RISK_NWPS_*` |
| NASA FIRMS fire detections | `https://firms.modaps.eosdis.nasa.gov/api` | API key | `lib/services/wildfire-service.ts` | `NASA_FIRMS_MAP_KEY`, `MODIS_MAP_KEY`, `FIRMS_*` |
| FEMA OpenFEMA | `https://www.fema.gov/api/open/v2` | none | `lib/services/openfema-service.ts` | `FEMA_OPEN_*`, `OPENFEMA_*` |
| InciWeb (RSS) | `https://inciweb.wildfire.gov` | UA + Referer | inside risk-ingest | `INCIWEB_*` |
| ESRI WFIGS wildfire perimeters | `services9.arcgis.com/.../WFIGS_Interagency_Perimeters_Current/...` | none | inside risk-ingest | — |
| Census (population context) | Census Bureau API | API key | `census-county-population.ts` | `CENSUS_API_KEY` |
| Google Maps | Maps + Places | API key | client + `places-service.ts` | `GOOGLE_MAPS_API_KEY`, `NEXT_PUBLIC_GOOGLE_MAPS_API_KEY` |
| OpenWeather | OpenWeather API | API key | client side | `NEXT_PUBLIC_OPENWEATHER_API_KEY` |

---

## §9 — Auth, Middleware, RBAC

### Cookies

| Cookie | Type | httpOnly | Purpose |
|--------|------|----------|---------|
| `session` | JWT (jose, HS256, 2 h TTL) | **yes** | Server-side identity |
| `userRole` | plain text | no | Client-side role gating |
| `accountStatus` | plain text | no | Client-side pending/approved gating |

### JWT payload (set in `/api/login`)

```ts
const session = await encrypt({
  user: {
    id, email, name, role, accountStatus,
    licenseId: user.licenseId?.toString() || null,
    responderVertical, responderFunction,
  },
  expires
});
```

Signing key = `process.env.JWT_SECRET` (with a dev fallback string in code — do
not rely on the fallback in prod).

### `middleware.ts` — route protection

Public paths: `/login`, `/signup`, `/_next/*`, `/favicon.ico`.

`accountStatus === 'pending'` → restricted to `/pending-approval` (except
logout).

Role → default landing route:

| Role | Lands on |
|------|----------|
| `super-admin` | `/super-admin-dashboard` |
| `admin`, `sub-admin`, `manager`, `observer` | `/admin-dashboard` |
| `eoc-manager`, `eoc-observer` | `/virtual-eoc` |
| `responder`, `public_official` | `/responder-dashboard` |
| `user` (default) | `/user-dashboard` |

Admin routes (gated to admin-ish roles):

```
/super-admin-dashboard, /admin-dashboard, /emergency-events, /alerts-communication,
/gis-mapping, /responders-agencies, /virtual-eoc-ai-center, /after-action-review,
/emergency-plan, /preparedness-information, /virtual-eoc-settings, /settings,
/sub-admin-settings, /admin/users, /ai-risk-assessment
```

Sub-admin-and-up only: `/admin/licenses`, `/admin/sub-admins`.

Responder-allowed (read-only on most admin views): `/responder-dashboard`,
`/responder-bed-status`, `/responder-field-status`,
`/responder-lodging-status`, `/responder-pharmacy-sites`,
`/responder-transit-deployment`, `/alerts-communication`,
`/virtual-eoc-settings`, `/responder-guides`, `/emergency-plan`, `/gis-mapping`.

Responder-exclusive (only `responder` / `public_official` allowed):
`/responder-dashboard`, `/responder-{bed,field,lodging,pharmacy-sites,transit-deployment,guides}-status`.

EOC roles blocked from `/admin/users`, `/admin/licenses`, `/admin/sub-admins`,
`/super-admin-dashboard`.

### Login flow (`/api/login`)

1. `bcrypt.compare(password, user.password)` (User has `password: { select: false }`)
2. Auto-provision two demo accounts on first sign-in:
   - `public_demo@yopmail.com` / `public_demo_pass` → `role: 'public_official'`
   - `nonprofit_demo@yopmail.com` / `nonprofit_demo_pass` → `role: 'responder'`, `responderVertical: 'nonprofit'`
3. Issue `session` JWT (`expires = now + 2h`) and set all three cookies
4. `recordActivity({ userId, action: 'LOGIN', label: …, meta: { email } })`

### Signup flow (`/api/signup`)

- Defaults `accountStatus: 'pending'`
- `role: 'sub-admin'` is unique per `(country, state, city)`
- Optional `responderInviteToken` validated against `ResponderInvite` (email
  match, not expired, not `usedAt`)

---

## §10 — UI Page Inventory

### App Router pages (44 total)

#### Admin (`app/(admin)/`)
| URL | File |
|-----|------|
| `/admin-dashboard` | `admin-dashboard/page.tsx` |
| `/admin/users`, `/admin/licenses`, `/admin/sub-admins` | `admin/{users,licenses,sub-admins}/page.tsx` |
| `/after-action-review` | `after-action-review/page.tsx` |
| `/ai-risk-assessment` | `ai-risk-assessment/page.tsx` |
| `/alerts-communication` | `alerts-communication/page.tsx` |
| `/emergency-events` | `emergency-events/page.tsx` |
| **`/emergency-plan`** | `emergency-plan/page.tsx` (deep-dive below) |
| `/gis-mapping` | `gis-mapping/page.tsx` |
| `/preparedness-information` | `preparedness-information/page.tsx` |
| `/responder-dashboard`, `/responder-field-status`, `/responder-lodging-status`, `/responder-settings`, `/responders-agencies` | `responder-*/page.tsx`, `responders-agencies/page.tsx` |
| `/settings`, `/sub-admin-settings`, `/virtual-eoc-settings`, `/virtual-eoc-ai-center` | `*/page.tsx` |
| `/super-admin-dashboard` | `super-admin-dashboard/page.tsx` |
| `/virtual-eoc`, `/virtual-eoc/{center,weather-traffic,maintenance,lodging,recovery}` | `virtual-eoc{,/<sub>}/page.tsx` |

#### User (`app/(user)/`)
| URL | File |
|-----|------|
| `/user-dashboard` | `user-dashboard/page.tsx` |
| `/user/alerts`, `/user/are-we-safe`, `/user/emergency-plan`, `/user/favorite-places`, `/user/my-locations`, `/user/news-updates`, `/user/plan`, `/user/preparedness`, `/user/resources`, `/user/settings`, `/user/weather` | `user/*/page.tsx` |
| `/active-shooter` | `active-shooter/page.tsx` |
| `/recovery` | `recovery/page.tsx` |

#### Public
| URL | File |
|-----|------|
| `/` | `app/page.tsx` (redirect logic in middleware) |
| `/login` | `app/login/page.tsx` |
| `/signup` | `app/signup/page.tsx` |
| `/pending-approval` | `app/pending-approval/page.tsx` |

### Components folder map

| Folder | Contents |
|--------|----------|
| `components/ui/` | Radix-based primitives — button, card, dialog, input, select, tabs, badge, accordion, etc. (~40 files) |
| `components/modals/` | Cross-page modals: SendCommunityAlert, SituationReport, DamageReport, RecoveryTools, ActivateVirtualEOC, NotifyLeaders, SafetyGuide, ScheduleCall, ActiveEmergencyEvents, AlertDetailModal |
| `components/providers/` | `AuthProvider` (session + role context) |
| `components/admin-dashboard/` | Dashboard tile cards: ai-risk-prediction-card, citizen-activity-feed, hospital-capacity-card, incident-overview-card, incident-timeline-card, key-impacts-card, power-outage-summary-card, real-time-resources-panel, resource-deployment-card, shelter-status-card |
| `components/responder/` | Per-vertical responder dashboard sections (electric, energy, federal, food-logistics, gas, water, hospital-capacity, hotel-availability, national-guard, nonprofit, pharmacy, police, transit, public-official) + `responder-info-bar` |
| `components/preparedness/` | `task-section-card`, `task-action-bar` |
| `components/` (root) | `AdminPageShell`, `AdminPageHeader`, `AdminPageLoader`, `sidebar`, `header`, `user-sidebar`, `responder-sidebar`, `theme-provider`, `session-idle-watcher`, `gis-map`, `google-map`, `leaflet-map`, `communications-center`, `threat-monitoring`, `quick-action-buttons`, `virtual-eoc-operations`, `setup-wizard` |

### Deep dive: `/emergency-plan` page (`app/(admin)/emergency-plan/page.tsx`, ~1 052 lines)

**The page in the screenshot.** It is the only place in the app that reads
the `aiIntegrity*` fields.

**Local types**:

```ts
type EmergencyAttachment = {
  _id?: string
  fileName: string
  fileUrl: string
  size: number
  uploadedAt: string | Date
  cloudinaryPublicId?: string
  cloudinaryResourceType?: 'image' | 'raw'
  aiIntegrityStatus?: string
  aiIntegrityScore?: number
  aiIntegritySummary?: string
  aiIntegrityAnalyzedAt?: string
}

type PlanCategory      = 'response' | 'coop' | 'bcp' | 'compliance'   // 'response' is synthetic
type StoredPlanCategory = 'coop' | 'bcp' | 'compliance'

type AuditSummary = {
  summary: string
  findings: string[]
  posture: 'Resilient' | 'Steady' | 'At Risk'
  averageScore: number
  totals:    { plans: number; attachments: number; analyzed: number }
  integrity: { inSync: number; reviewing: number; deviation: number; unanalyzed: number }
  generatedAt: string
}
```

**Stat-card config** (DOCUMENT_CATEGORY_META — lines 76–87):

```ts
[
  { key: 'response',   name: 'Response Plans',       icon: Zap,         color: 'text-amber-500',   bg: 'bg-amber-500/10' },
  { key: 'coop',       name: 'COOP Protocols',       icon: Shield,      color: 'text-blue-500',    bg: 'bg-blue-500/10' },
  { key: 'bcp',        name: 'Business Continuity',  icon: Folder,      color: 'text-purple-500',  bg: 'bg-purple-500/10' },
  { key: 'compliance', name: 'Compliance Vault',     icon: CheckCircle, color: 'text-emerald-500', bg: 'bg-emerald-500/10' },
]
```

**Client-side category inference** (when `category` is missing or 'response',
which never gets stored):

```ts
function inferCategoryFromPlanId(planId: string): PlanCategory {
  const id = planId.toLowerCase()
  if (/hurricane|earthquake|flood|wildfire|tornado|tsunami|severe|weather|national|dispatch|response|citizen|alert|evacuation/.test(id)) return 'response'
  if (/staff|human|personnel|hr|employee|workforce|succession|essential|vital.?records|devolution/.test(id)) return 'coop'
  if (/telecom|communicat|it|network|technical|critical|system|data|supply|vendor|facility/.test(id))         return 'bcp'
  return 'compliance'
}
```

**Integrity badge code** (the only consumer of `aiIntegrityStatus` /
`aiIntegrityScore`):

```ts
function integrityPresentation(status: string | undefined, score: number | undefined) {
  const s = status || 'Reviewing'
  const pct = Math.min(100, Math.max(0, typeof score === 'number' && !Number.isNaN(score) ? score : 0))
  let labelColor = 'text-blue-500'
  let barColor   = 'bg-blue-500'
  if (s === 'In Sync')         { labelColor = 'text-emerald-500'; barColor = 'bg-emerald-500' }
  else if (s === 'Deviation Found') { labelColor = 'text-red-500'; barColor = 'bg-red-500' }
  return { labelColor, barColor, pct }
}
```

**Client API calls** the page makes:

| Method + URL | Purpose |
|--------------|---------|
| `GET    /api/admin/emergency-plans` | Initial list load |
| `POST   /api/admin/emergency-plans` | Upload file → triggers AI integrity in route |
| `PATCH  /api/admin/emergency-plans` | Edit plan label / overview / category |
| `POST   /api/admin/emergency-plans/steps` | Save plan steps |
| `DELETE /api/admin/emergency-plans/attachment` | Remove attachment |
| `GET    /api/admin/emergency-plans/audit-summary` | Cached audit (page load) |
| `POST   /api/admin/emergency-plans/audit-summary` | Refresh audit (click button) |

**Audit panel** renders `auditSummary.summary` + `auditSummary.findings[]`
verbatim and `auditSummary.posture` as a coloured badge.

---

## §11 — Integration Contract for the Python Service

Two transport options. The Python team picks one (or both).

> **Locked (ARCHITECTURE §1.3): Option A.1 — REST synchronous, Next.js writes Mongo.**
> Option B (Redis) is retained below only as a future scale path if upload volume
> demands a queue; do not build it for v1.

Shared assumptions (apply to both):

- Trigger fires after step 5 of the upload flow (i.e. after `plan.save()` so
  `attachment._id` exists) but **replaces** the in-process call to
  `openaiService.analyzeCoopAttachmentIntegrity`.
- Canonical idempotency key: **`attachment._id`** (MongoDB ObjectId).
  Strongly recommended secondary: `SHA-256(file bytes)` for content-hash dedup
  so re-uploads of identical bytes don't re-spend tokens.
- The badge in the UI shows `'Reviewing'` until the integrity fields are
  populated, so **async ack is acceptable**. Latency budget: best-effort
  ≤ 30 s, soft cap ≤ 5 min.

### Shared input payload

```jsonc
{
  "tenantContext": {
    "tenantKey": "<licenseId or city|state|country>",
    "actorUserId": "<User._id of uploader>"
  },
  "plan": {
    "planId":   "pandemic-coop-plan",
    "label":    "Pandemic COOP Plan",
    "overview": "Plan overview text...",
    "category": "coop",                  // 'coop' | 'bcp' | 'compliance'
    "steps":    ["Step 1...", "Step 2..."]
  },
  "attachment": {
    "attachmentId":             "<EmergencyPlan.attachments[i]._id as hex>",
    "fileName":                 "pandemic-coop-plan.pdf",
    "fileExtension":            "pdf",   // 'pdf' | 'docx' | 'csv' | 'xlsx'
    "fileMime":                 "application/pdf",
    "fileSizeBytes":            123456,
    "fileUrl":                  "https://res.cloudinary.com/<account>/raw/upload/v.../earthquick/emergency-plans/...",
    "cloudinaryPublicId":       "earthquick/emergency-plans/...",
    "cloudinaryResourceType":   "raw"
  },
  "vectorKey":   "<attachmentId or sha256>",
  "modelHints":  { "preferredModel": "embedding-v1", "maxTokens": 380 }
}
```

### Shared response payload

```jsonc
{
  "status":  "In Sync",       // | "Reviewing" | "Deviation Found"
  "score":   84,              // integer 0..100
  "summary": "Plan covers succession, vital records, and pandemic response steps; alignment with overview is strong.",
  "analyzedAt": "2026-05-30T12:34:56.789Z",
  "vectorIds":  ["<id1>", "<id2>"],     // optional, returned for traceability
  "modelVersion": "integrity-v1",
  "details": {                          // optional, not consumed by current UI
    "componentScores": { "content": 80, "name": 90, "category": 85, "quality": 95, "duplication": 70 },
    "similarFiles":    [{ "attachmentId": "...", "similarity": 0.91 }]
  }
}
```

### 11.A — Option A: REST webhook (Next.js → Python) (CHOSEN)

Next.js calls a Python-owned HTTPS endpoint synchronously (or fire-and-forget)
on every upload + audit request.

**Endpoint (Python-owned)**:
- `POST /v1/integrity/analyze` — per-file
- `POST /v1/audit/summary` — aggregate (payload = the `ContinuityAuditInput`
  shape — see §6.7)

**Auth**: HMAC header (`X-Ready2Go-Signature: hex(HMAC_SHA256(secret, body))`)
or simple shared `Authorization: Bearer <secret>` with secret in
`process.env.PYTHON_INTEGRITY_TOKEN`.

**Writeback**:
- Option A.1 — **Python returns JSON; Next.js writes**. Lowest blast radius;
  no second MongoDB connection. Implementation: replace the
  `openaiService.analyzeCoopAttachmentIntegrity(...)` call in the upload
  handler with `httpClient.post(PYTHON_URL + '/v1/integrity/analyze', payload)`
  and feed the response into the existing `EmergencyPlan.updateOne` block.
- Option A.2 — **Python writes to MongoDB directly**, then `POST`s a tiny
  webhook back to Next.js so the UI can re-fetch. Requires Python to hold a
  Mongo connection.

Pros: simple, easy to debug, no broker required.
Cons: synchronous coupling; if Python is down, the upload handler must time
out gracefully.

### 11.B — Option B: Redis queue (Next.js producer → Python consumer) (DEFERRED — future scale path)

Use the already-declared `ioredis` dependency. Add a single Redis instance and
two streams.

| Stream | Producer | Consumer | Payload |
|--------|----------|----------|---------|
| `r2g:integrity:incoming` | Next.js upload handler | Python | shared input payload |
| `r2g:integrity:results`  | Python | Next.js (optional poller) | shared response payload |
| `r2g:audit:incoming`     | Next.js audit POST     | Python | `ContinuityAuditInput` |
| `r2g:audit:results`      | Python | Next.js (optional)        | `ContinuityAuditSummary` |

Use `XADD` (consumer groups via `XGROUP CREATE` and `XREADGROUP`) so multiple
Python workers can scale horizontally without double-processing.

**Writeback**: Python writes directly to MongoDB (use a separate Mongoose-equiv
driver — `motor` or `pymongo`). Schema reference is §4.2 and §4.3. Always
match selectors as `{ planId, 'attachments._id': ObjectId(attachmentId) }`.

Pros: decoupled, retry/backoff is trivial, surfaces operate at their own pace.
Cons: needs a Redis instance and operational ownership of the consumer group.

### 11.C — Aggregate audit contract (shared by both options)

`buildAuditInput()` in `app/api/admin/emergency-plans/audit-summary/route.ts`
produces this shape (`ContinuityAuditInput`):

```ts
{
  totals: { plans: number; attachments: number; analyzed: number };
  averageScore: number;
  counts:    { coop: number; bcp: number; compliance: number; response: number };
  integrity: { inSync: number; reviewing: number; deviation: number; unanalyzed: number };
  plans: Array<{
    planId: string;
    label: string;
    category: 'coop' | 'bcp' | 'compliance' | 'response';
    attachmentCount: number;
    stepCount: number;
    attachments: Array<{ fileName: string; status?: string; score?: number; summary?: string }>;
  }>;
}
```

The Python service must return `ContinuityAuditSummary`:

```ts
{
  summary: string;                 // ≤360 chars, no markdown
  findings: string[];              // 2..4 bullets, each ≤140 chars
  posture: 'Resilient'|'Steady'|'At Risk';
  averageScore: number;
}
```

…which the existing `POST /audit-summary` route persists with the totals +
integrity counts (so you can either return only the four fields above, or the
full document — both work).

> Note: the contract target is `summary ≤360` / `findings ≤140 each`. The current
> Next.js code (§6.5) slices defensively to 420/160; the Python service should emit
> to the **360/140** contract.

---

## §11.5 — Operations, Rollout, Coexistence

### Hosting today

- Next.js → **Vercel** (env vars `VERCEL_URL`, `APP_URL`,
  `NEXT_PUBLIC_APP_URL`)
- MongoDB → Atlas (shared cluster)
- Cloudinary → SaaS (folder `earthquick/emergency-plans`)
- Redis → **not provisioned yet**; required for Option B

### Suggested Python deployment posture

- Separate container/host (Fly.io / Render / ECS) reachable from Vercel by URL
  (Option A) or with shared Redis access (Option B).
- Either way: own connection pools for MongoDB and OpenAI/vector-store.

### Coexistence strategies for cutover

1. **Shadow mode** *(safest)* — Python runs in parallel and writes to a
   separate field (e.g. `aiIntegrityScoreV2`, `aiIntegrityStatusV2`). UI
   continues reading legacy fields. After N days of parity, flip a flag and
   rename V2 → primary in a single migration.
2. **Feature flag** *(simple)* — env var `INTEGRITY_BACKEND=python|legacy` at
   the upload route; flip at deploy time.
3. **Hard cutover** *(aggressive)* — remove
   `openaiService.analyzeCoopAttachmentIntegrity` from the upload route, route
   all writes through Python. Bundle with a backfill of historical
   attachments.

### Backfill

Existing attachments in prod won't auto-rescore. Plan one of:
- Next.js script that pages `EmergencyPlan.find({})`, enqueues every
  `attachment._id` to the Python service.
- Python-owned endpoint `POST /v1/integrity/rescan` that takes
  `{ attachmentIds: string[] }`.

### Idempotency / dedup

Recommend storing on the Python side, per attachment:

```
{ attachmentId, contentHash, fileUrl, lastAnalyzedAt, modelVersion, vectorIds[], scoreComponents }
```

If `contentHash` is unchanged AND `modelVersion` is unchanged → return cached
verdict, skip the LLM/embedding call. This is the single biggest cost lever.

### Observability handoff

The Next.js side has **no structured logging of AI calls** today — just
`console.error` on failure. Python service should log every request/response
from day one. A reasonable target: a new MongoDB collection
`AIIntegrityCall { timestamp, attachmentId, requestPayload, responsePayload,
modelVersion, tokenUsage, latencyMs, success }`.

### Cost note (the user's stated motivation)

Today: one OpenAI chat completion per integrity call (~8 k input tokens + ~80
output tokens) **plus** one per audit refresh. With many uploads this is the
dominant cost. Vector embedding + content-hash caching + small-model fallback
should cut this by ≫ 10×.

---

## §11.6 — Out-of-Scope / Legacy Note

- `pages/` directory exists at the root but is the legacy Next.js Pages
  Router. No `pages/api/**` routes are wired up — everything lives under
  `app/api/`.
- Root JSON fixtures (`nws_active_alerts.json`, `usgs_river_data.json`,
  `noaa_nwps_gauges.json`, `nasa_firms_fires.json`, `model.json`) are dev
  snapshots, not authoritative. Ignore for the integration.
- `seeddata.js` populates a dev MongoDB. Use it as a reference for test-data
  shapes if your team wants to reproduce a populated vault locally.
- `testsprite_tests/`, `scratch/` are scratch/throwaway folders.

---

## §12 — Constants & Enums Reference (verbatim)

### `EmergencyPlan.attachments[i].aiIntegrityStatus`

```
"In Sync" | "Reviewing" | "Deviation Found"
```

**Case-sensitive.** Mongoose does not validate (the field has no enum), but
the UI keys colours / behaviour on these exact strings.

### `ContinuityAudit.posture`

```
"Resilient" | "Steady" | "At Risk"   // enum-validated in the schema
```

### `EmergencyPlan.category` (stored)

```
"coop" | "bcp" | "compliance"        // enum-validated
```

The UI also displays a synthetic `"response"` category, but **never stores it
in the DB** — it's inferred client-side from `planId` regex (see §10).

### `UnifiedEvent` enums

```
source     = nws | usgs | earthquake | nwps | fema | nasa_firms | inciweb |
             noaa_nwis | noaa_ncei | manual | seed
category   = flood | earthquake | wildfire | storm | marine | coastal_surf |
             hazardous | tsunami | volcanic | landslide | winter_weather |
             air_quality | extreme_heat | fema_declaration
severity   = Low | Moderate | High | Extreme
type       = Warning | Watch | Advisory | Statement | Declaration
iconType   = cloud | triangle | lightning | flame | wave | snowflake | wind
status     = Take Action | Get Prepared | Monitor | Info
dataStatus = current | past
```

### `User.role` (9 values)

```
super-admin | sub-admin | admin | observer | responder | manager | user |
eoc-manager | eoc-observer | public_official
```

### `User.accountStatus`

```
pending | approved | rejected
```

### `RESPONDER_VERTICALS` (19 values — `lib/responder-verticals.ts`)

```
general-responder | hospital | healthcare-hospital (legacy) | police | hotel |
pharmacy | medical-logistics | transit | utility-electric | utility-gas |
utility-water | utility-energy | food-logistics | telecom | national-guard |
federal | state-government | nonprofit | public-official
```

### `DOCUMENT_CATEGORY_META` (the 4 stat-card buckets in the UI)

```ts
[
  { key: 'response',   name: 'Response Plans',      icon: Zap,         color: 'text-amber-500',   bg: 'bg-amber-500/10' },
  { key: 'coop',       name: 'COOP Protocols',      icon: Shield,      color: 'text-blue-500',    bg: 'bg-blue-500/10' },
  { key: 'bcp',        name: 'Business Continuity', icon: Folder,      color: 'text-purple-500',  bg: 'bg-purple-500/10' },
  { key: 'compliance', name: 'Compliance Vault',    icon: CheckCircle, color: 'text-emerald-500', bg: 'bg-emerald-500/10' },
]
```

### Posture derivation rules (verbatim)

```ts
if (!input.totals.plans)                                       return 'At Risk';
if (deviations > 0 || avg < 55 || analyzed === 0)              return 'At Risk';
if (reviewing > 0 || avg < 75)                                 return 'Steady';
return 'Resilient';
```

---

## §13 — Glossary

| Term | Meaning |
|------|---------|
| **COOP** | Continuity Of Operations Plan — essential functions, succession, vital records, hazard playbooks |
| **BCP** | Business Continuity Plan — IT/telecom DR, network, supply chain, RTO/RPO |
| **Compliance Vault** | Regulatory / audit artifacts: NIMS/ICS, HIPAA, OSHA, training registers |
| **Response Plan** | UI-only synthetic bucket for hazard-response playbooks (inferred from planId regex; not stored as `category`) |
| **AAR** | After Action Review |
| **AI Integrity** | Per-file score + label produced by AI assessing whether a file matches its declared plan context |
| **In Sync** | Content substantively supports plan context |
| **Reviewing** | Partial / unclear / weak alignment / needs human review |
| **Deviation Found** | Serious gap or wrong intent vs plan |
| **Resilient / Steady / At Risk** | Aggregate posture for the whole continuity vault (driven by avg score + integrity counts) |
| **UnifiedEvent** | Central event collection — used by the AI Risk Assessment page |
| **dataStatus** | `current` vs `past` flag on UnifiedEvent (separates live events from historical context) |
| **EOC** | Emergency Operations Center |
| **NWS** | US National Weather Service |
| **USGS** | US Geological Survey |
| **NWPS** | NOAA National Water Prediction Service |
| **NASA FIRMS** | Fire Information for Resource Management System (hotspot detection) |
| **OpenFEMA** | FEMA OpenData API (disaster declarations) |
| **InciWeb** | Federal incident-information clearinghouse (RSS) |
| **WFIGS** | Wildland Fire Interagency Geospatial Services (perimeters) |
| **FEMA** | US Federal Emergency Management Agency |

---

## §14 — Known Weaknesses of the Current AI Flow

Listed in the order you should probably address them:

1. **No chunking past 8 000 chars** — long PDFs are silently truncated;
   integrity verdict reflects only the prefix.
2. **Silent extraction failure** — `extractTextFromBuffer` returns `''` on any
   parser error, AI is called anyway, and the model is instructed to "score
   conservatively (35–55)" which means scan-only PDFs always look weak.
3. **No vector store** — every integrity call inlines the full plan context.
4. **No caching** — uploading the identical bytes a second time spends
   tokens again. (Suggested fix: content-hash dedup.)
5. **No retry** — `callOpenAI` swallows errors and returns the static
   fallback (`Reviewing`, score 50, "Analysis unavailable").
6. **No token counting** — payloads can blow past `max_tokens`; output may
   truncate mid-JSON, then `JSON.parse` throws, then fallback fires.
7. **Magic thresholds** — `derivePosture` uses hard-coded 55 and 75, not
   calibrated to any baseline.
8. **Naive regex** — `inferCategory()` / `inferCategoryFromPlanId()` match
   keywords in the filename/plan id; weak signal.
9. **N+1 audit query** — `EmergencyPlan.find({})` then in-memory walk; works
   today, will hurt at thousands of plans.
10. **No structured logging** of AI calls — no audit trail of what was sent
    or received.

---

## §15 — Open Questions for the Python Team

Decide these before building, then update this section.

> Several of these are now **locked** in ARCHITECTURE §1.3 / §9.1 — annotated below.

1. **Writeback authority.** Does the Python service write to MongoDB directly,
   or return JSON to a Next.js callback that writes? (Affects auth surface,
   schema-drift risk, and whether Python needs a Mongo driver.)
   — ✅ RESOLVED: Next.js writes Mongo; Python returns JSON (ARCH §1.3).
2. **Transport choice.** Option A (REST) or Option B (Redis streams)? Both?
   — ✅ RESOLVED: REST synchronous (ARCH §1.3).
3. **Vector store vendor.** Pinecone, Qdrant, Weaviate, pgvector, Chroma, …?
   Self-hosted or SaaS? Per-tenant index or global?
   — ✅ RESOLVED: Weaviate (WCD managed) (ARCH §1.3).
4. **Canonical vector key.** `attachment._id` (simplest) or
   `(planId, contentHash)` (best for dedup)? Recommend storing both.
5. **Coexistence path.** Shadow mode, feature-flag, or hard cutover (see
   §11.5)?
   — ✅ RESOLVED: shadow → feature flag → cutover (ARCH §9.1).
6. **Backfill trigger.** Manual cron, one-off script, or Python-owned
   `/v1/integrity/rescan`?
7. **Embedding model choice.** OpenAI `text-embedding-3-small`,
   `text-embedding-3-large`, Cohere, local model? Same model for filename
   embeddings and content embeddings, or separate?
   — ✅ RESOLVED: OpenAI text-embedding-3-small (ARCH §1.3).
8. **Latency SLO.** Badge shows `'Reviewing'` until populated, so async is
   OK — what's the target p95 for that to update? (Suggested: ≤ 30 s
   happy-path, ≤ 5 min hard ceiling.)
9. **Audit refresh trigger.** On every upload? Periodic cron? UI-driven
   only?
10. **Legacy cleanup.** Once Python is live, do we delete
    `openaiService.{inferCoopPlanMetadata, analyzeCoopAttachmentIntegrity,
    generateContinuityAuditSummary}` from the Next.js code, or keep them as
    fallbacks behind a flag?

---

## Appendix A — Existing root documentation status

| File | Status | Reuse? |
|------|--------|--------|
| `PROJECT_ARCHITECTURE.md` | Tech-stack section accurate; **roles section stale** (lists 3, actual = 9) | Partially — superseded by §3 + §9 of this doc |
| `ARCHITECTURE_DIAGRAMS.md` | Auth flow still accurate; component tree partially stale | Partially — superseded by §9 + §10 |
| `doc/one-minute-polling.md` | Current | Quoted in §8 |
| `doc/zone-matching.md` | Current | Reference for NWS UGC zone matching |
| `docs/development-plan.md` | Active AI Risk Assessment spec — out-of-scope for integrity service | Reference for §6.5 awareness |
| `docs/responders info.md` | Responder verticals taxonomy | Cross-checked against §12 |
| `docs/fixes-implementation-plan.md`, `docs/api-testing-guide.md`, `docs/cloudinary-upload-testing-guide.md` | Internal QA / sprint notes | Skip |

**This document supersedes all of the above** for the Python team's purposes.

---

*End of `docs/PROJECT_CONTEXT.md`.*
