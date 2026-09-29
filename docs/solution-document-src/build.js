// Generates the FleetPulse Solution Document following the hackathon template's structure.
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, Table, TableRow, TableCell, WidthType, ShadingType,
  AlignmentType, ImageRun, Header, Footer, PageNumber, LevelFormat, TableOfContents, PageBreak, BorderStyle,
  TabStopType,
} = require("docx");

const R = JSON.parse(fs.readFileSync(path.join(__dirname, "results.json"), "utf8"));
const TPL = path.join(__dirname, "tpl", "word", "media");
const OUT = process.argv[2] || path.join(__dirname, "FleetPulse_Solution_Document.docx");
const FONT = "Calibri";
const W = 9638; // A4 text width in DXA with 2 cm margins

const t = (text, o = {}) => new TextRun({ text, font: FONT, size: 21, ...o });
const P = (text, o = {}) => new Paragraph({ spacing: { after: 100 }, ...o, children: Array.isArray(text) ? text : [t(text)] });
const B = (label, text) => P([t(label + ": ", { bold: true }), t(text)]);
const H1 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_1, spacing: { before: 280, after: 120 }, children: [new TextRun({ text, font: FONT, bold: true, size: 30, color: "1F3864" })] });
const H2 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_2, spacing: { before: 200, after: 80 }, children: [new TextRun({ text, font: FONT, bold: true, size: 25, color: "2F5496" })] });
const bullet = (text, level = 0) => new Paragraph({ numbering: { reference: "bullets", level }, spacing: { after: 60 },
  children: Array.isArray(text) ? text : [t(text)] });
const bullets = (items) => items.map((x) => bullet(x));
const code = (text) => text.split("\n").map((line) => new Paragraph({ spacing: { after: 0 }, shading: { type: ShadingType.CLEAR, fill: "F3F4F6", color: "auto" },
  children: [new TextRun({ text: line || " ", font: "Consolas", size: 17 })] }));

function table(header, rows, widths) {
  const total = widths.reduce((a, b) => a + b, 0);
  const scale = W / total;
  const ws = widths.map((w) => Math.floor(w * scale));
  ws[ws.length - 1] += W - ws.reduce((a, b) => a + b, 0);
  const cell = (text, i, head) => new TableCell({
    width: { size: ws[i], type: WidthType.DXA },
    shading: head ? { type: ShadingType.CLEAR, fill: "D9E2F3", color: "auto" } : undefined,
    margins: { top: 40, bottom: 40, left: 80, right: 80 },
    children: String(text).split("\n").map((line) => new Paragraph({ children: [new TextRun({ text: line, font: FONT, size: 18, bold: !!head })] })),
  });
  return new Table({
    width: { size: W, type: WidthType.DXA }, columnWidths: ws,
    rows: [new TableRow({ tableHeader: true, children: header.map((h, i) => cell(h, i, true)) }),
           ...rows.map((r) => new TableRow({ children: r.map((c, i) => cell(c, i, false)) }))],
  });
}
const gap = () => new Paragraph({ spacing: { after: 60 }, children: [] });

function image(file, widthPx) {
  const data = fs.readFileSync(file);
  const w = data.readUInt32BE(16), h = data.readUInt32BE(20); // PNG IHDR
  return new ImageRun({ type: "png", data, transformation: { width: widthPx, height: Math.round(widthPx * h / w) } });
}

// ------------------------------------------------------------------ content
const children = [];
const push = (...xs) => xs.flat().forEach((x) => children.push(x));

// Cover
push(
  new Paragraph({ spacing: { before: 1800, after: 200 }, children: [new TextRun({ text: "Connected Vehicle Intelligence Hackathon", font: FONT, size: 44, bold: true, color: "1F3864" })] }),
  new Paragraph({ spacing: { after: 400 }, children: [new TextRun({ text: "Solution Document: FleetPulse, predictive maintenance for connected fleets", font: FONT, size: 30, color: "2F5496" })] }),
  B("Submitted by", R.team_name),
  B("Team Members & Roles", R.team_members),
  B("Problem Space Chosen", "Predictive maintenance: which vehicle components are likely to need maintenance or fail in the next 7 days, ranked by expected cost"),
  B("Repository URL", R.repo_url),
  B("Demo Video URL (at most 5 min)", R.video_url),
  B("Date of Submission", R.date),
  new Paragraph({ children: [new PageBreak()] }),
  H1("Table of Contents"),
  new TableOfContents("Contents", { hyperlink: true, headingStyleRange: "1-2" }),
  P([t("(Right-click and choose Update Field in Word to refresh page numbers.)", { italics: true, color: "666666", size: 18 })]),
  new Paragraph({ children: [new PageBreak()] }),
);

// 1
push(H1("1. Executive Summary"),
  P("Unplanned breakdowns are the most expensive event in a commercial fleet: the repair, the towing, the missed deliveries and the idle driver all land at once. FleetPulse is aimed at the fleet manager, who today learns about most failures when a vehicle stops or a warning light is already on."),
  P("FleetPulse ingests OEM telemetry for 100,000 simulated vehicles through Kafka. It turns the stream into exact rolling-window features per component, and scores each brake, powertrain and battery with a calibrated 7-day failure probability. The result is a ranked maintenance queue priced in expected cost, with alerts, behind a secure API and a web dashboard."),
  P("Key results (all measured, evidence in the repository):"),
  ...bullets([
    `100,000 simulated vehicles seeded (300,000 components); ${R.fleet100k_rows} telemetry rows for the whole fleet, plus a 500-vehicle, 90-day cohort (12.17M rows) with ground truth used for training.`,
    `Calibrated 7-day risk: test PR-AUC ${R.pt_prauc} for powertrain (prevalence ${R.pt_prev}) and ${R.batt_prauc} for battery (prevalence ${R.batt_prev}), i.e. about 21x and 15x better than chance; brake is weakly predictable (${R.brake_prauc} vs ${R.brake_prev}) and reported as such.`,
    "Streaming and offline features are proven identical: 803,279 values compared, 0 mismatches, including 15 percent duplicate replay and a crash between processing steps.",
    `Throughput on one laptop: Kafka broker ${R.kafka_produce} events/s produced, ${R.kafka_consume} events/s consumed by one consumer; stream processor 1,162 events/s per process; API ${R.api_summary}.`,
    "Freshness and resilience: ingest to calibrated risk in 1.7 s (p95); a Kafka broker killed mid-run caused no duplicates and the pipeline recovered on its own.",
    "Two OEM formats (OEM-A flat, OEM-B nested in US units) through the same pipeline, and an AI fleet assistant (Groq LLM tool-calling) with guardrails and audit.",
    `Security and quality: JWT with roles and tenant isolation, audit of every data request, rate limiting, location minimisation, right to erasure; ${R.n_tests} automated tests; Semgrep 0 findings, Bandit 0 open issues, pip-audit 0 known vulnerabilities.`,
  ]),
  P("Unique ideas:"),
  ...bullets([
    "A simulator with hidden physical degradation, so every prediction can be checked against ground truth.",
    "One mergeable statistic definition shared by the streaming and batch paths, which removes training/serving skew.",
    "Priority by expected dollar loss instead of raw probability.",
  ]),
);

// 2
push(H1("2. Problem Statement & Validation"), H2("2.1 Problem Statement"),
  P([t("Fleet managers need a way to know which vehicle components will need maintenance or fail in the next 7 days, and which of those matter most, because unplanned breakdowns cost far more than planned service and today are mostly discovered only after a warning light or a roadside stop.", { italics: true })]),
  B("Primary user", "Fleet manager of a mixed ICE, EV and hybrid commercial fleet"),
  B("Secondary stakeholders", "Maintenance planners and workshops (scheduling), drivers (safety), finance (downtime cost), OEMs (warranty signals)"),
  H2("2.2 Evidence & Validation"),
  ...bullets([
    "Facts: breakdowns carry direct repair cost plus downtime.",
    "Facts: connected vehicles already stream the signals needed (temperatures, voltages, deceleration, DTCs).",
    "Facts: most fleets still use time- or mileage-based service intervals.",
    "Assumptions: the cost figures and the degradation physics in the simulator.",
  ]),
  table(["Evidence / Assumption", "Source or Method", "What It Shows", "Confidence"], [
    ["Unplanned repair plus downtime costs several times a planned service", "Industry practice; cost table in component_cost (assumed values: failure USD 1,800-12,000, downtime 16-72 h at USD 95/h)", "A correct early warning is worth thousands of dollars per component", "Medium"],
    ["Degradation leaves observable traces before failure (temperature offsets, voltage sag, braking deficit)", "Simulation with hidden wear, calibrated so no single raw signal is a giveaway (max univariate AUC 0.69)", "Prediction needs multi-signal windows, not thresholds", "Medium (synthetic)"],
    ["Rules on DTCs fire too late", "Simulator emits persistent DTCs only once a component is already in MAINTENANCE_REQUIRED", "Rules alone give zero lead time; ML adds days of warning", "High (by design of the test)"],
    ["About 39 percent of components have an event in 90 days; about 3 percent of hourly snapshots are positive", "Calibration report (docs/phase3-calibration.md)", "Heavy class imbalance: PR-AUC, not accuracy", "High (measured)"],
  ], [26, 30, 28, 12]),
  gap(),
  B("Existing alternatives", "OBD-II dongles (hardware cost, one vehicle at a time); OEM portals (one brand each, no cross-fleet ranking); rule-based telematics alerts (react to DTCs, no lead time, no cost ranking); Motorq Fuse (flags issues and dollar impact; FleetPulse focuses specifically on component-level 7-day failure probability with calibrated, auditable models)."),
  H2("2.3 Impact & Success Metrics"),
  table(["Metric", "Baseline Today", "Target", "How Measured / Estimated"], [
    ["Failure events with a warning at least 1 day ahead (powertrain)", "0 (rules fire at failure)", "> 40 percent", `Back-test on the final test window: recall ${R.pt_recall} at the chosen threshold`],
    ["Precision of the top 1 percent of the ranked queue (powertrain)", `${R.pt_prev} (random)`, "> 50 percent", `Back-test: ${R.pt_top1}`],
    ["Expected 7-day loss visible to the manager", "Not available", "Every scored component", "Priority queue; COST_DATA_REQUIRED instead of zero when costs are missing"],
    ["Critical alert latency", "n/a", "< 5 s", "Rule engine latency log (ingested_at to alert)"],
  ], [30, 18, 16, 36]),
  gap(),
  B("Scale of impact", "At 10K vehicles, about 3,900 of 10,000 components have an event per quarter in the simulation; converting even 30 percent of those from unplanned to planned service avoids hundreds of tow-and-repair incidents. At 100K vehicles the same model runs unchanged; only partitions and consumers scale."),
  B("Wider impact", "Safety (brake and battery failures on the road), cost (downtime), compliance (audit trail and data minimisation)."),
);

// 3
push(H1("3. Solution Description"), H2("3.1 Solution Overview & User Journey"),
  P("FleetPulse watches every vehicle's telemetry and, every simulated hour, estimates for each brake, powertrain and battery the probability that it will need maintenance or fail within 7 days. It multiplies that probability by the component's failure and downtime cost and shows the fleet manager a ranked list of what to service first."),
  ...bullets([
    "Vehicle event: the OEM cloud pushes telemetry, which arrives on Kafka.",
    "Validation: the VIN is checked, then the payload is normalised to the canonical schema.",
    "Detection (two independent paths):",
  ]),
  bullet("A rule engine raises DTC alerts within seconds.", 1),
  bullet("The stream processor deduplicates, stores the telemetry and updates rolling windows.", 1),
  ...bullets([
    "Prediction: hourly snapshots are scored with a calibrated 7-day probability.",
    "Priority: expected loss ranks the maintenance queue.",
    "Alert: an ML alert is raised when the probability crosses the component threshold.",
    "Action: the fleet manager opens the vehicle, sees components, trends and service history, and acknowledges the alert.",
    "Outcome: a planned service replaces an unplanned breakdown.",
  ]),
);
if (R.screenshot && fs.existsSync(R.screenshot)) {
  push(new Paragraph({ alignment: AlignmentType.CENTER, children: [image(R.screenshot, 600)] }),
    P([t("Figure 1: FleetPulse dashboard (fleet manager view): KPIs, maintenance priority by 7-day expected loss, live alerts.", { italics: true, size: 18 })]));
}
push(H2("3.2 Key Value Proposition"),
  B("Customer job", "Keep the fleet on the road at the lowest maintenance cost."),
  B("Pain relieved", "Surprise breakdowns, towing, idle drivers, missed deliveries."),
  B("Gain created", "A 7-day look-ahead per component with a dollar value, so service can be booked before failure and the budget goes to the riskiest, most expensive components first."),
  B("Differentiation", "Calibrated probabilities (they mean what they say, so expected losses add up); features proven identical in streaming and training; every alert, access and erasure is audited."),
  H2("3.3 Innovative Ideas"),
  table(["Idea", "What Is New", "Evidence"], [
    ["Ground-truth simulator with hidden physics", "Vehicles carry hidden wear per component that drives subtle observable changes; maintenance records are the labels. Calibrated so no single signal gives the answer away.", "docs/phase3-calibration.md: per-signal AUC table, prevalence 38-40 percent, stationarity per third of the run"],
    ["Exact, mergeable windows with one shared definition", "Fixed buckets of mergeable statistics in Redis, updated by one atomic Lua script that is also the dedup mark; the same code computes features offline", "Parity: 803,279 values, 0 mismatches, max relative difference 1.9e-10, with duplicate replay and a crash/restart"],
    ["Expected-loss priority with honest missing-data handling", "Queue ordered by P(7d) x (failure cost + downtime x hourly cost); missing cost gives COST_DATA_REQUIRED, never zero", "maintenance_priority view and API; tests for ordering and pagination"],
  ], [22, 45, 33]),
);

// 4
push(H1("4. Feature List"),
  P("Status is Done unless stated. Video timestamps follow the demo script in docs/demo-script.md."),
  table(["ID", "Feature", "User Story", "Priority", "Status", "Code Path", "Video"], [
    ["F-01", "100K-vehicle simulator with hidden degradation and ground truth", "As a platform team I want realistic multi-OEM data so that models can be validated", "Must", "Done", "simulator/", "03:00"],
    ["F-02", "Kafka ingestion with VIN validation and DLQ", "As a platform team I want bad payloads isolated so that the pipeline never stalls", "Must", "Done", "services/identity-resolver/", "03:20"],
    ["F-03", "Stateless OEM normalisation (adapter factory), OEM-A and OEM-B", "As a platform team I want to onboard OEM formats without downtime", "Must", "Done", "services/normalization/, contracts/oem/", "03:20"],
    ["F-04", "Exactly-once-effect dedup (Bloom + Redis + Lua)", "As an analyst I want duplicates never counted twice", "Must", "Done", "services/stream-processor/", "03:40"],
    ["F-05", "Real-time DTC rule alerts (< 5 s)", "As a fleet manager I want critical faults immediately", "Must", "Done", "services/rule-engine/", "01:50"],
    ["F-06", "Exact 5m / 1h / 24h streaming features + offline parity", "As a data scientist I want identical features in training and serving", "Must", "Done", "fleetpulse_features/, .../features/", "03:40"],
    ["F-07", "Leakage-safe labels and chronological dataset", "As a data scientist I want honest evaluation", "Must", "Done", "fleetpulse_features/labels.py, scripts/build_dataset.py", "-"],
    ["F-08", "Calibrated 7-day risk models vs baseline", "As a fleet manager I want probabilities I can trust", "Must", "Done", "ml/", "02:20"],
    ["F-09", "Expected-loss maintenance priority queue", "As a fleet manager I want to know what to fix first", "Must", "Done", "db/postgres/migrations/005-006, services/api/", "01:10"],
    ["F-10", "ML alerts with audit", "As a fleet manager I want to be told when risk crosses a threshold", "Must", "Done", "ml/score.py", "01:30"],
    ["F-11", "Secure REST API (JWT, RBAC, tenant isolation, keyset pagination, rate limit)", "As a customer I want my fleet data isolated and fast", "Must", "Done", "services/api/app/", "02:40"],
    ["F-12", "Web dashboard", "As a fleet manager I want one screen for risk and alerts", "Must", "Done", "services/api/app/static/index.html", "01:00"],
    ["F-13", "Audit log, location minimisation, right to erasure", "As a DPO I want compliance evidence", "Should", "Done", "services/api/app/main.py", "02:50"],
    ["F-14", "Containers, CI, K8s manifests, Terraform (AWS)", "As an operator I want repeatable deployment on any cloud", "Should", "Done (not applied to a cloud)", ".github/, infra/", "04:00"],
    ["F-15", "Fleet assistant (LLM tool-calling, guardrails, audit)", "As a manager I want to ask questions in natural language", "Could", "Done", "services/api/app/assistant.py", "02:30"],
    ["F-16", "Chaos test: Kafka broker kill", "As an operator I want proof the pipeline recovers", "Should", "Done", "scripts/chaos_kafka.sh", "04:00"],
  ], [6, 22, 26, 8, 9, 20, 7]),
);

// 5
push(H1("5. Solution Architecture (High-Level Design)"), H2("5.1 Architecture Overview"),
  B("System context", "Fleet managers and admins use the dashboard and API over HTTPS with JWT. OEM clouds push telemetry. The platform uses a cloud secret manager and Prometheus-compatible metrics. Diagrams (Mermaid, rendered on GitHub): docs/architecture.md."),
  ...code(`Simulator -> [oem.inbound] -> Identity resolver -> [vehicle.raw] -> Normalizer -> [vehicle.normalized]
   vehicle.normalized -> Stream processor -> TimescaleDB (telemetry, features) + Redis (dedup, windows)
   vehicle.normalized -> Rule engine -> PostgreSQL (alert + audit) -> [vehicle.alerts]
   Redis latest snapshot -> Risk scorer -> PostgreSQL (component_risk, ML alerts, priority view)
   Browser -> FastAPI (JWT) -> PostgreSQL / TimescaleDB / Redis
   Invalid or unknown VIN -> [vehicle.dlq]`),
  gap(),
  table(["Hop", "Work", "Expected latency"], [
    ["Simulator -> oem.inbound", "Envelope keyed by VIN", "< 10 ms"],
    ["Identity resolver", "VIN check digit, VIN -> vehicle_id (cached)", "5-20 ms"],
    ["Normalizer", "OEM-A v1 -> canonical, schema validated", "< 5 ms"],
    ["Rule engine", "DTC rule, alert + audit in one transaction", "20-100 ms (target < 5 s)"],
    ["Stream processor", "dedup, batch insert, Lua per event, snapshot", "0.3-1 s per 500-event batch"],
    ["Risk scorer -> dashboard", "5 s scoring loop, 5 s dashboard refresh", "typically < 10 s"],
  ], [30, 45, 25]),
  H2("5.2 Technology Stack & Justification"),
  table(["Layer", "Choice", "Why This, and What You Rejected"], [
    ["Ingestion / Messaging", "Kafka (KRaft), JSON Schema contracts", "Partitioned ordering per vehicle and replay; rejected RabbitMQ (weak replay) and exactly-once transactions (latency, sink coupling)"],
    ["Stream / Batch", "Python consumers; numpy/pandas batch; Parquet", "Shared code between streaming and batch was the priority; Flink/Spark rejected for the hackathon (team skill, local footprint); Flink is the scale path"],
    ["Relational / NoSQL / Cache / Vector", "PostgreSQL 16, TimescaleDB, Redis 7; no vector store", "ACID + FKs for business state; hypertables for time series; Redis for atomic sub-ms state; vector store not justified by any decision"],
    ["Backend / Frontend", "FastAPI, psycopg2 pools; single-page HTML/JS", "OpenAPI out of the box, simple sync SQL; no build step for the UI"],
    ["ML / AI", "scikit-learn (LogisticRegression baseline, HistGradientBoosting), Platt calibration", "Tabular data, NaN-native trees, fast CPU training; deep learning not needed"],
    ["Infrastructure / CI-CD / Observability", "Docker Compose, Kubernetes (kustomize), Terraform (AWS), GitHub Actions, Prometheus metrics", "Cloud-agnostic images and manifests; overlays only change endpoints"],
  ], [18, 30, 52]),
  H2("5.3 Data Architecture"),
  B("ER diagram", "3NF relational core (tenant, fleet, vehicle, vehicle_component, driver, trip, maintenance_event, alert, audit_log, component_risk, component_cost, app_user) in docs/er-diagram.md. Tenant ownership is derived through vehicle -> fleet -> tenant, never copied. Deliberate denormalisation: the maintenance_priority_mv read model (CQRS), refreshed concurrently after each scoring pass."),
  table(["Data", "Store", "CAP"], [
    ["Tenants, fleets, vehicles, components, alerts, audit, users, risk, cost, ground truth", "PostgreSQL", "CP"],
    ["Telemetry, hourly feature snapshots", "TimescaleDB hypertables", "CP (replicas for reads)"],
    ["Dedup keys, window buckets, latest snapshot, rate limits, caches", "Redis", "AP (bounded: a lost key costs one idempotent re-write)"],
    ["Event log", "Kafka", "AP with acks=all in production"],
    ["Datasets, archives, models", "Parquet on object storage", "n/a"],
  ], [55, 25, 20]),
  gap(),
  B("Capacity", "100K vehicles x 1 event/s = 100K events/s; about 1 KB/event -> 8.6 TB/day raw, about 3 PB/year; partition key vehicle_id (Kafka) and event_ts (Timescale chunks of 7 days). Hot: Redis (25 h of window buckets, 15 min dedup). Warm: Timescale 30 days then native compression (about 10x). Cold: Parquet on object storage for 2 years."),
  P("Query optimisation (EXPLAIN ANALYZE, full plans in docs/algorithms-and-sql.md):"),
  table(["Query", "Before (ms)", "After (ms)", "Change Made"], R.explain_rows, [40, 13, 13, 34]),
  H2("5.4 Deployment View"),
  ...bullets([
    "Local: one command, docker compose up, starts Kafka, PostgreSQL, TimescaleDB, Redis, the four pipeline services, the API, the scorer and the simulator.",
    "Cloud (AWS, Terraform in infra/terraform/aws): VPC over 3 AZs, private EKS, Multi-AZ RDS, 3-broker MSK with TLS, Redis replication group, Secrets Manager, KMS envelope encryption.",
    "Kubernetes (infra/k8s): API Deployment with HPA 2-20 and a PDB; stream processors scale up to the partition count; restricted pod security; NetworkPolicy default deny; TLS 1.3 ingress.",
    "Cloud-agnostic: identical images and manifests. The GCP overlay only changes endpoints; secrets come through External Secrets.",
    "Validation status: terraform validate passes and kustomize renders both overlays. Nothing was applied to a real cloud account.",
  ]),
);

// 6
push(H1("6. Low-Level Design"), H2("6.1 Layering & Separation of Concerns"),
  P("Style: layered services with ports-and-adapters at the edges (OEM adapters, sinks, context providers). Dependencies point inward: routes -> queries (SQL only) -> connection pools; the feature engine depends on abstract context providers and snapshot writers, so the same engine runs against Postgres in production and in-memory providers in tests."),
  table(["Layer", "Responsibility", "Must Not"], [
    ["Presentation / API", "HTTP, validation (patterns, enums, page limits), JWT and role checks, DTO shaping", "Contain SQL or business rules (SQL lives in queries.py)"],
    ["Application / Service", "Batch pipeline (pipeline.process_batch), scoring loop, dataset build", "Depend on a specific broker client (messages are plain dicts)"],
    ["Domain", "Feature statistics and formulas, labels, lifecycle rules, priority formula", "Import framework or database code (fleetpulse_features is pure numpy)"],
    ["Infrastructure", "Kafka clients, Redis Lua, Postgres/Timescale sinks, context cache", "Leak vendor types into the domain"],
  ], [22, 45, 33]),
  gap(),
  ...code(`fleetPulse/
  simulator/            engine, components, sinks, serializers
  fleetpulse_features/  stats, features, labels, dataset (pure, shared)
  services/             identity-resolver, normalization, stream-processor, rule-engine, api
  ml/                   model wrapper, train, score
  scripts/              generation, features, dataset, parity, verification, load tests
  db/                   postgres and timescale DDL + migrations
  infra/                k8s (kustomize), terraform/aws
  configs/  contracts/  docs/  tests/`),
  H2("6.2 Design Principles Applied"),
  B("SOLID", "Single responsibility (queries.py vs main.py; stats vs features); open/closed OEM AdapterFactory (a new OEM is a new adapter class); dependency inversion in FeatureEngine (context_provider, snapshot_writer injected)."),
  B("12-Factor", "All configuration from the environment (.env.example, ConfigMap); stateless API and scorer; disposable consumers that resume from committed offsets."),
  B("Idempotency, fail-fast, least privilege, DRY, KISS", "Deterministic ids and ON CONFLICT everywhere; config validated at load (bucket widths must divide windows); non-root containers and dropped capabilities; one shared feature definition; plain SQL instead of an ORM."),
  H2("6.3 Design Patterns Used"),
  table(["Pattern", "Problem It Solves in Your System", "Location in Code"], [
    ["Adapter + Factory", "Maps each OEM payload format and version to one canonical event", "services/normalization/normalizer.py"],
    ["Repository / Query object", "Keeps SQL out of route handlers", "services/api/app/queries.py"],
    ["CQRS read model", "Fast ranked queue without joining five tables per request", "maintenance_priority_mv"],
    ["Retry with backoff / fail-closed", "Redis or database outage halts the batch; no offset commit, no data loss", "stream_processor/pipeline.py retry_forever"],
    ["Cache-aside", "Summary KPIs cached 5 s per tenant", "services/api/app/main.py summary"],
    ["Dead-letter queue", "Poison messages isolated instead of crash-looping", "identity-resolver send_to_dlq"],
    ["Strategy", "Two model kinds behind one CalibratedModel interface", "ml/model.py"],
  ], [22, 50, 28]),
  H2("6.4 Interfaces, Contracts & Runtime Flows"),
  B("API contract", "OpenAPI generated by FastAPI at /docs and /openapi.json; versioned under /api/v1; keyset pagination with opaque cursors; errors as {\"error\": {\"code\", \"message\"}}; 429 with Retry-After at 300 requests per minute per principal."),
  B("Event schemas", "JSON Schema contracts in contracts/ (OEM-A v1, canonical vehicle_event, alert_event). Topics keyed by VIN or vehicle_id. Evolution: additive optional fields; the adapter factory dispatches on (oem_id, schema_version) so a new version runs side by side."),
  P("Critical flow 1, duplicate delivery:"),
  ...bullets([
    "The event arrives a second time after a consumer restart.",
    "The dedup check hits Redis and drops the event.",
    "If it slips through (race), the Lua SET NX returns 0 and no statistics change.",
    "The Timescale ON CONFLICT DO NOTHING makes the sink write idempotent.",
    "Offsets are committed.",
  ]),
  P("Critical flow 2, failure path (crash mid-batch):"),
  ...bullets([
    "The sink write succeeds, then the Lua scripts are applied.",
    "The process crashes before the snapshot step and the offset commit.",
    "On restart the batch is redelivered.",
    "The dedup stage drops every event, because the keys exist.",
    "The snapshot step still runs for every vehicle in the batch, so the missing snapshot is produced exactly once.",
    "This path is exercised in tests/test_feature_lua.py and the parity restart variant.",
  ]),
  H2("6.5 Algorithms & Data Structures"),
  table(["Problem", "Algorithm", "Complexity", "Scale Tested"], [
    ["Duplicates at 100K events/s", "Rotating Bloom pair + Redis authority + atomic Lua", "O(k) per event; 103 MB per filter at n = 90M, p = 1 percent", "7,463 duplicate deliveries dropped in parity; 1,016 live"],
    ["Exact sliding windows", "Mergeable bucket statistics, merge at snapshot", "O(S) per event; O(B x S) per snapshot; 358 KB per vehicle", "49,018 events, 12,667 snapshots identical to offline"],
    ["Offline windows", "Hourly group-by + sliding_window_view merge", "O(N + H x S)", "12.2M events -> 2.7M snapshots in 547 s"],
    ["Leakage-safe labels", "Sorted events + binary search", "O((T + E) log E)", "2.7M snapshots; brute-force check on 3,000 samples"],
    ["VIN validation", "Regex + ISO 3779 check digit", "O(17)", "2,000 VINs"],
    ["Ranked queue pagination", "B-tree on (loss, id) + keyset", "O(log n + k) per page", "see 5.3"],
  ], [20, 32, 26, 22]),
  P("Pseudocode for the per-event Lua update and snapshot catch-up: docs/algorithms-and-sql.md."),
);

// 7
push(H1("7. Non-Functional Requirements & Performance Benchmarks"),
  table(["NFR", "Target (Case Study)", "Achieved", "How Measured"], R.nfr_rows, [16, 22, 36, 26]),
  gap(),
  B("Load test setup", "Everything on one 8-core, 8 GB MacBook running Docker Desktop (single Kafka broker, single PostgreSQL, TimescaleDB and Redis container). Kafka: kafka-producer-perf-test (2M x 1 KB records, 12 partitions, lz4). API: scripts/load_api.py, concurrent authenticated clients for 60 s across five endpoints. Streaming features: scripts/verify_feature_parity.py timing."),
  B("Honest gap", `The 100K events/s target is not demonstrated end to end on this hardware. The broker alone sustains ${R.kafka_produce} events/s here; the Python stream processor with per-event Lua updates handles about 1,160 events/s per process (up from 784 after packing the Redis state). Reaching 100K/s is a horizontal-scaling path (12+ partitions, 3 brokers, one consumer per partition, and moving the per-event work to batched Lua or Flink), which the Kubernetes and Terraform packs are sized for but which was not load-tested.`),
);

// 8
push(H1("8. Security & Compliance"),
  B("Threat model", "STRIDE in docs/threat-model.md (12 threats with control and honest status). Top five: forged API callers, cross-tenant reads, event replay inflating risk, API flooding, privilege escalation by viewers. All five are mitigated and tested."),
  B("Authentication & authorisation", "JWT (HS256, 1 h expiry, issuer checked) issued after PBKDF2-SHA256 password verification; roles ADMIN, FLEET_MANAGER, VIEWER; tenant isolation on every query through vehicle -> fleet -> tenant (tests show 404 across tenants). Production path: OIDC with RS256 from an identity provider."),
  B("Device identity & encryption", "VIN validation and DLQ at ingestion; TLS 1.3 ingress, TLS to MSK and Redis, AES-256 at rest via KMS (RDS, ElastiCache, EKS secrets) in Terraform; secrets from Secrets Manager via External Secrets; nothing secret in git (.env ignored, Semgrep secrets rules clean). Device mTLS is designed, not implemented."),
  B("Privacy (GDPR, DPDP Act 2023)", "Synthetic data only; location rounded to about 1 km for non-admin roles; driver erasure endpoint nulls personal fields, unlinks trips and writes an audit row; retention tiers in docs/architecture.md."),
  B("Audit", "Every authenticated data request, login, acknowledgement, alert creation (rule and ML) and erasure writes an audit_log row with actor, action, path and time. Request-path audit rows are batched off the critical path (at most about 1 s at risk on a hard crash)."),
  B("AI safety", "The fleet assistant can only call four read-only tools that run the API's tenant-scoped SQL with the tenant taken from the JWT, so prompt injection cannot widen access. Arguments are validated, questions are capped at 500 characters and 4 model steps, every question and tool call is audited, and a rules fallback answers if the LLM is unavailable. ML alerts record model name, version and probability."),
);

// 9
push(H1("9. Test Strategy"),
  table(["Test Type", "Tools", "No. of Tests", "Coverage / Result", "In CI?"], R.test_rows, [18, 24, 10, 34, 14]),
  gap(),
  B("Edge cases covered", "Duplicate events (in-batch, cross-batch, after restart), late events beyond retention, events exactly at the snapshot boundary, event exactly at t + 7 days, snapshots while awaiting service, unobserved label horizon, missing signals (NaN, not zero), unknown or invalid VIN, cross-tenant access, malformed cursors, oversized page requests, rate-limit breach, driver erasure."),
  B("Report links", "Coverage HTML and XML, JUnit XML, Bandit, Semgrep and pip-audit outputs in the CI artifacts; local copies referenced in docs/phase4-verification.md."),
);

// 10
push(H1("10. Observability"),
  ...bullets([
    "API: Prometheus endpoint /metrics with request counters by method, route and status, and latency histograms. Every response carries X-Response-Time-ms.",
    "Stream processor: /metrics with dedup counters (accepted, duplicates dropped, Bloom fast-path hits, Redis reads) and feature counters (events applied, duplicates skipped, late events ignored, snapshots written, snapshot latency p50/p95).",
    "Rule engine: logs the ingest-to-alert latency for every alert.",
    "Kafka consumer lag: read with kafka-consumer-groups (used by the verification scripts).",
  ]),
  P("Troubleshooting a latency spike:"),
  ...bullets([
    "Check the API histogram to see whether one route or all routes are slow.",
    "If all routes: check database pool saturation and Redis latency.",
    "If one route: run EXPLAIN ANALYZE on its query (see 5.3).",
    "If dashboard freshness lags while the API is fast: check consumer lag on vehicle.normalized and the stream processor's snapshot latency p95.",
    "Scale consumers up to the partition count, or look for a stuck batch in the retry-with-backoff logs.",
  ]),
  B("Gap", "No Grafana dashboard or distributed tracing is shipped; metrics are exposed in Prometheus format for scraping."),
);

// 11
push(H1("11. AI / ML Component"),
  B("Purpose", "Estimate the probability that each component needs maintenance or fails within 7 days, so work can be planned before failure. Rules on DTCs only fire once a component is already failing; they give no lead time."),
  B("Data & features", "Hourly snapshots of 44 to 77 features per component: 5m / 1h / 24h windows of speed, braking, temperatures, voltage, current, state of charge, DTC counts; plus days and distance since service, vehicle age and type. Label: event in (t, t + 7 d]. Leakage checks: only telemetry with event_ts < t (tests delete or perturb all later data and assert identical features), in-gap and unobserved-horizon exclusions, chronological splits with 7-day buffers, 20 percent vehicle holdout."),
  B("Model design", "Per component: logistic regression baseline and HistGradientBoosting, class weighted; Platt calibration on the calibration window; alert threshold at maximum F1 on the threshold window; served model chosen on the threshold window; the test window is used once."),
  P("Evaluation on the final test window (8 days, never used for fitting or selection):"),
  table(["Component", "Prevalence", "LR PR-AUC", "GBM PR-AUC", "Served", "Served ROC-AUC", "Brier", "Precision@top1%", "Holdout PR-AUC"], R.ml_rows, [13, 10, 10, 10, 9, 11, 9, 14, 14]),
  gap(),
  P("Reading the table:"),
  ...bullets([
    "Powertrain and battery risk are strongly predictable.",
    "Brake risk is only 3x the baseline: brake wear shows only during harsh braking, which is rare.",
    "Precision at the top 1 percent for powertrain means almost every one of the highest-risk alerts is a real upcoming event.",
  ]),
  B("Guardrails & cost", "Probabilities are calibrated (Brier 0.017 to 0.030); missing costs never produce a zero loss; every ML alert records model version and probability; inference is CPU-only, well under 1 ms per snapshot."),
  B("Agent design", "POST /api/v1/assistant uses Groq's OpenAI-compatible chat API (free tier, llama-3.3-70b-versatile) with tool-calling over fleet_summary, top_priority, active_alerts and vehicle_detail. The system prompt forbids invented numbers and treats tool data as data. Without a key, or on an LLM error, a deterministic keyword router answers from the same tools. Rules-mode latency is about 5 ms; the LLM path is bounded by 4 steps and a 20 s timeout."),
);

// 12
push(H1("12. Architecture Decisions, Risks & Future Enhancements"),
  table(["ADR", "Decision", "Key Consequence"], [
    ["0001", "Kafka at-least-once, idempotent consumers, manual commits", "Replays are safe; duplicates are handled by every sink"],
    ["0002", "PostgreSQL (CP) + TimescaleDB + Redis (AP); PACELC: favour latency on read models", "Three stores to run; consistency where money and audit live"],
    ["0003", "Bloom fast path, Redis authority, atomic Lua dedup + feature update", "Crash-safe; replay beyond the 15-min dedup TTL needs rebuild + warm-up"],
    ["0004", "Exact fixed windows from mergeable buckets, one shared definition", "Proven parity; 0.14 MB Redis per vehicle (packed)"],
    ["0005", "Calibrated per-component models, chronological splits, expected-loss priority", "Honest metrics; brake is weak and reported as such"],
  ], [8, 52, 40]),
  gap(),
  P("Risks and technical debt:"),
  ...bullets([
    "The end-to-end 100K events/s target is not demonstrated (see section 7).",
    "Redis feature state extrapolates to about 14 GB at 100K vehicles (139 KB per vehicle, packed).",
    "The simulator's producer lost 0.45 percent of messages during a 39 s broker outage (retries exhausted); a local producer outbox would close this.",
    "The 100K fleet has 8h48m of simulated history; the 90-day ground-truth cohort is 500 vehicles.",
    "Terraform and Kubernetes are validated but not applied to a cloud account.",
    "HS256 JWT instead of an identity provider; no device mTLS.",
    "No BDD, Pact or DAST suites; chaos covers a broker kill and a stream-processor restart, not pod kills.",
    "The LLM path of the assistant is tested against a mocked Groq response; it is live only when a key is configured.",
  ]),
  P("Next three steps from prototype to pilot:"),
  ...bullets([
    "Move the per-event streaming work to batched Lua or Flink and run a 3-broker, 12-partition load test at 100K events/s.",
    "Replace HS256 with OIDC, add device mTLS, and wire Prometheus + Grafana + OpenTelemetry tracing.",
    "Pilot with real OEM data and workshop feedback to refine the cost table, and let the assistant draft work orders for human approval.",
  ]),
);

// 13
push(H1("13. Demo Video (5 Minutes Maximum)"),
  table(["Time", "Segment", "What to Show"], [
    ["0:00 - 0:30", "Problem", "The fleet manager, the cost of a surprise breakdown, and the fact that about 39 percent of components need service within a quarter"],
    ["0:30 - 1:00", "Solution", "One line: a 7-day, component-level failure probability, priced and ranked"],
    ["1:00 - 3:00", "Live demo", "Login; KPIs; priority queue; open a high-risk vehicle; trends and service history; live stream with an injected DTC raising a rule alert within seconds; acknowledge; tenant isolation with the viewer account"],
    ["3:00 - 4:15", "Under the hood", "Architecture; parity result; model report vs baseline; Kafka and API benchmarks; stream-processor restart with no duplicates"],
    ["4:15 - 5:00", "Impact & next steps", "Measured results, honest gaps, the path to 100K events/s, team"],
  ], [14, 16, 70]),
  B("Video link", R.video_url),
  P("A step-by-step script with commands is in docs/demo-script.md."),
);

// 14
push(H1("14. Repository Checklist"),
  table(["Item", "Status"], [
    ["README: problem, architecture, quick start, environment variables, test commands, known issues", "Done (README.md, docs/)"],
    ["One-command run: docker compose up with the simulator", "Done; data bootstrap scripts documented in README"],
    ["Structure: service folders, /docs, /infra, /tests", "Done"],
    ["CI: build, all test suites and security scans on every push", "Done (.github/workflows/ci.yml)"],
    ["Hygiene: no secrets committed, .env.example provided", "Done"],
    ["Final tag v1.0-submission", "Done at submission"],
  ], [70, 30]),
);

// 15
push(H1("15. Conclusion"),
  B("Learnings", "Evidence before claims: several of our own early \"passing\" checks turned out never to have run. We now require pasted output for every result. Parity tests between streaming and batch caught subtle issues, such as float32 storage precision, that would otherwise have become silent model skew."),
  B("Strengths", "Idempotency end to end; proven feature parity; calibrated and honestly evaluated models; a ranked, cost-aware output a fleet manager can act on; security and audit built in."),
  B("Challenges solved", "Degradation had to be realistic without making any single signal a giveaway; the event rate had to be stationary over time; crashes between processing steps had to be made safe; a laptop with 8 GB of RAM had to hold a 100K-vehicle fleet."),
);

// 16
push(H1("16. Declarations"),
  B("Open-source components", "Python 3.11, FastAPI (MIT), Uvicorn (BSD), psycopg2 (LGPL), redis-py (MIT), kafka-python-ng (Apache-2.0), numpy (BSD), pandas (BSD), pyarrow (Apache-2.0), scikit-learn (BSD), PyJWT (MIT), PyYAML (MIT); Apache Kafka (Apache-2.0), PostgreSQL (PostgreSQL License), TimescaleDB (Apache-2.0 edition), Redis 7 (BSD); Terraform AWS modules (Apache-2.0)."),
  B("AI tools used", "Claude (Anthropic) via Claude Code was used as a coding assistant for implementation, tests, documentation and this document. The product's fleet assistant calls the Groq API (openai/gpt-oss-120b) when a key is configured. Design decisions, review and verification were done by the team."),
  B("Data", "All data is synthetic, produced by the project's own simulator; it contains no real personal or vehicle-owner data."),
);

// 17
push(H1("17. Appendix"),
  ...bullets([
    "docs/feature-spec.md: every feature name, formula and unit.",
    "docs/phase3-calibration.md: simulator calibration with per-signal AUC.",
    "docs/phase4-verification.md: pasted outputs of the verification runs.",
    "docs/algorithms-and-sql.md: pseudocode and full query plans.",
    "docs/threat-model.md, docs/adr/: STRIDE and decisions.",
    "data/models/m1/report.json: full model evaluation, produced by ml/train.py.",
  ]),
);

// ------------------------------------------------------------------ document
const header = new Header({ children: [new Paragraph({
  tabStops: [{ type: TabStopType.RIGHT, position: W }],
  children: [image(path.join(TPL, "image2.png"), 95), new TextRun("\t"), image(path.join(TPL, "image1.png"), 130)],
})] });
const footer = new Footer({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, children: [
  new TextRun({ children: ["Page ", PageNumber.CURRENT], font: FONT, size: 16, color: "666666" })] })] });

const doc = new Document({
  creator: "FleetPulse team", title: "FleetPulse Solution Document",
  styles: { default: { document: { run: { font: FONT, size: 21 } } } },
  features: { updateFields: true },
  numbering: { config: [{ reference: "bullets", levels: [
    { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 270 } } } },
    { level: 1, format: LevelFormat.BULLET, text: "◦", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 1080, hanging: 270 } } } },
  ] }] },
  sections: [{ properties: { page: { margin: { top: 1300, bottom: 1134, left: 1134, right: 1134 } } },
               headers: { default: header }, footers: { default: footer }, children }],
});
Packer.toBuffer(doc).then((buf) => { fs.writeFileSync(OUT, buf); console.log("wrote", OUT, buf.length, "bytes"); });
