"""Assembles results.json for the Solution Document from evidence files in the repository."""
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.json")
L = lambda p: open(os.path.join(REPO, p)).read()

r = json.load(open(os.path.join(REPO, "data/models/m1/report.json")))
c = r["components"]


def sel(k):
    return c[k][c[k]["selected"]]


ml_rows = []
for k in ("POWERTRAIN", "BATTERY", "BRAKE"):
    s = sel(k)
    ml_rows.append([k, f"{s['test']['prevalence']:.3f}", f"{c[k]['logreg']['test']['pr_auc']:.3f}", f"{c[k]['hgb']['test']['pr_auc']:.3f}",
                    "GBM" if c[k]["selected"] == "hgb" else "LR", f"{s['test']['roc_auc']:.3f}", f"{s['test']['brier']:.4f}",
                    f"{s['test']['precision_at_top_1%']:.2f}", f"{s['test_vehicle_holdout'].get('pr_auc', 'n/a')}"])

load = json.load(open(os.path.join(REPO, "data/logs/load_api.json")))
o = load["overall_ms"]
api = f"API p95 {o['p95']} ms / p99 {o['p99']} ms at {load['requests_per_second']} requests/s with 0 errors"

rule_latency = sys.argv[1] if len(sys.argv) > 1 else "see verification log"
snap = sys.argv[2] if len(sys.argv) > 2 else "n/a"

res = {
    "team_name": "Kshitij Gupta",
    "team_members": "Kshitij Gupta (kg0237@srmist.edu.in)",
    "repo_url": "https://github.com/guptaksh17/fleetpulse",
    "video_url": "https://www.youtube.com/watch?v=g7IFX9hf6rQ",
    "date": "01/10/2026",
    "n_tests": 95,
    "fleet100k_rows": "7,626,000",
    "pt_prauc": f"{sel('POWERTRAIN')['test']['pr_auc']:.2f}", "pt_prev": f"{sel('POWERTRAIN')['test']['prevalence']:.3f}",
    "batt_prauc": f"{sel('BATTERY')['test']['pr_auc']:.2f}", "batt_prev": f"{sel('BATTERY')['test']['prevalence']:.3f}",
    "brake_prauc": f"{sel('BRAKE')['test']['pr_auc']:.2f}", "brake_prev": f"{sel('BRAKE')['test']['prevalence']:.3f}",
    "pt_recall": f"{sel('POWERTRAIN')['test']['recall']:.2f}", "pt_top1": f"{sel('POWERTRAIN')['test']['precision_at_top_1%']:.2f}",
    "kafka_produce": "65,848", "kafka_consume": "41,417",
    "api_summary": api,
    "ml_rows": ml_rows,
    "explain_rows": [
        ["Tenant priority queue, top 25 by expected loss (251,456 scored components)", "881.5", "0.45", "Read model maintenance_priority_mv + index (tenant_id, loss DESC, id DESC)"],
        ["Vehicle list page 1,001 of 100,708 vehicles", "51.9", "0.32", "Keyset pagination instead of OFFSET 50000"],
        ["Admin audit log page", "33.5", "0.38", "Composite index matching sort and keyset (idx_audit_time_id)"],
    ],
    "nfr_rows": [
        ["Ingest throughput", "100K+ events/s; 3x burst 5 min", "Kafka (1 laptop broker): 65,848 events/s produced (1 KB, lz4), 41,417/s consumed by 1 consumer. Stream processor with features: 1,162 events/s per Python process (packed Redis state). 100K/s end to end NOT demonstrated; burst not tested.", "kafka-producer/consumer-perf-test; parity timing"],
        ["End-to-end latency", "< 2 s dashboard; < 5 s critical alert", f"Critical rule alert: {rule_latency}. ML risk: ingest to risk in database p50 1.70 s / p95 1.73 s (event-driven scorer); dashboard polls every 2 s, so about 2-4 s to screen.", "Rule engine latency log; SQL over telemetry.ingested_at, component_features.computed_at, component_risk.scored_at (3,123 snapshots)"],
        ["API latency", "p95 < 200 ms; p99 < 500 ms", f"p95 {o['p95']} ms, p99 {o['p99']} ms, {load['requests_per_second']} req/s, 0 errors (20 clients, 60 s, 100K-vehicle data)", "scripts/load_api.py"],
        ["Resilience", "Recovers after broker / pod failure", "Kafka broker SIGKILLed for 39 s mid-run: 0 duplicate rows, dedup invariant held (14314 - 11942 = 2372), recovered without restarts; 0.45 percent of attempted messages lost at the producer. Stream-processor restart and crash/replay also verified.", "scripts/chaos_kafka.sh; verify_phase2.sh; parity restart variant"],
        ["Availability", "99.9 percent, no single point of failure", "Designed: Multi-AZ RDS, 3-broker MSK, Redis failover, HPA + PDB. Not measured.", "Terraform / manifests"],
    ],
    "test_rows": [
        ["Unit", "pytest / unittest, coverage.py", "95 in suite", "72 percent total line coverage; core modules 91-99 percent (features, labels, dedup, engine, simulator models)", "Yes"],
        ["Integration & contract", "Real Redis (Lua, leakage, parity), real PostgreSQL / TimescaleDB (API, assistant), JSON Schema contract tests (OEM-A, OEM-B)", "32 + scripts", "All pass locally; Redis tests run in CI, database tests skip in CI. No Pact.", "Partial"],
        ["Acceptance (BDD)", "Scripted scenarios: smoke_live.sh, verify_phase2/3/4.sh", "4 scripts", "Pass (see verification docs). No Cucumber/behave.", "No"],
        ["Performance / Load / Soak", "kafka perf tests, scripts/load_api.py", "3 runs", f"Kafka 65.8K/s produce; API p95 {o['p95']} ms / p99 {o['p99']} ms. No soak test.", "No"],
        ["Security", "Semgrep, Bandit, pip-audit, Trivy", "4 tools", "Semgrep 0 findings; Bandit 0 open (8 reviewed false positives); pip-audit 0 known vulnerabilities; Trivy runs in CI", "Yes"],
        ["Compliance & chaos", "API tests, chaos_kafka.sh, restart tests", "6 + 1 script", "Audit trail and erasure verified; Kafka broker kill, stream-processor restart and crash/replay verified; no pod kill", "Partial"],
    ],
    "snapshot_latency": snap,
    "screenshot": os.path.join(REPO, "docs/images/ui_pulse.png"),
}
json.dump(res, open(OUT, "w"), indent=1)
print("wrote", OUT)
