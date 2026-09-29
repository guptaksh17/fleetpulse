"""
FleetPulse REST API (Phase 6).

Layers: routes (this module) -> queries (queries.py, SQL only) -> db pools (db.py).
Security: JWT bearer tokens (HS256, secret from the environment), roles ADMIN / FLEET_MANAGER /
VIEWER, tenant isolation (non-admin users only see vehicles whose fleet belongs to their tenant),
Redis fixed-window rate limiting per principal, an audit_log row for every data request, location
coarsened to about 1 km for non-admin users, and a driver erasure endpoint.
Pagination is keyset-based everywhere (opaque cursor), never OFFSET.
"""

import base64
import json
import os
import queue
import threading
import time
from collections import defaultdict
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from . import queries
from .db import cursor, redis_client, ts_pool
from .security import Principal, decode_token, issue_token, verify_password

API = "/api/v1"
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "300"))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

app = FastAPI(title="FleetPulse API", version="1.0.0",
              description="Predictive maintenance: 7-day component risk, priority queue, alerts, vehicles.")
bearer = HTTPBearer(auto_error=False)

# ----------------------------------------------------------------------------- metrics
_BUCKETS = (5, 10, 25, 50, 100, 200, 500, 1000, 5000)
_req_count = defaultdict(int)
_lat_hist = defaultdict(lambda: [0] * (len(_BUCKETS) + 1))
_lat_sum = defaultdict(float)


@app.middleware("http")
async def observe(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - t0) * 1000
    route = request.scope.get("route")
    path = getattr(route, "path", "unmatched")
    _req_count[(request.method, path, response.status_code)] += 1
    h = _lat_hist[(request.method, path)]
    h[next((i for i, b in enumerate(_BUCKETS) if ms <= b), len(_BUCKETS))] += 1
    _lat_sum[(request.method, path)] += ms
    response.headers["X-Response-Time-ms"] = f"{ms:.1f}"
    return response


@app.get("/metrics", include_in_schema=False)
def metrics():
    lines = ["# TYPE fleetpulse_api_requests_total counter"]
    for (m, p, s), n in sorted(_req_count.items()):
        lines.append(f'fleetpulse_api_requests_total{{method="{m}",path="{p}",status="{s}"}} {n}')
    lines.append("# TYPE fleetpulse_api_request_ms histogram")
    for (m, p), h in sorted(_lat_hist.items()):
        cum = 0
        for b, c in zip(list(_BUCKETS) + ["+Inf"], h):
            cum += c
            lines.append(f'fleetpulse_api_request_ms_bucket{{method="{m}",path="{p}",le="{b}"}} {cum}')
        lines.append(f'fleetpulse_api_request_ms_sum{{method="{m}",path="{p}"}} {_lat_sum[(m, p)]:.1f}')
        lines.append(f'fleetpulse_api_request_ms_count{{method="{m}",path="{p}"}} {cum}')
    return PlainTextResponse("\n".join(lines) + "\n")


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.status_code, "message": exc.detail}},
                        headers=getattr(exc, "headers", None))


# ----------------------------------------------------------------------------- auth
def principal(request: Request, creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer)) -> Principal:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(401, "missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    try:
        p = decode_token(creds.credentials)
    except Exception:
        raise HTTPException(401, "invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
    _rate_limit(f"user:{p.user_id}")
    _audit(p, request)
    return p


def require(*roles):
    def dep(p: Principal = Depends(principal)) -> Principal:
        if p.role not in roles:
            raise HTTPException(403, f"role {p.role} may not perform this action")
        return p
    return dep


def _rate_limit(key: str):
    window = int(time.time() // 60)
    k = f"rl:{key}:{window}"
    try:
        r = redis_client()
        n = r.incr(k)
        if n == 1:
            r.expire(k, 65)
    except Exception:
        return  # graceful degradation: the limiter fails open if Redis is unavailable
    if n > RATE_LIMIT_PER_MINUTE:
        raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": str(60 - int(time.time()) % 60)})


_audit_q: "queue.Queue" = queue.Queue(maxsize=100_000)
AUDIT_FLUSH_SECONDS = float(os.getenv("AUDIT_FLUSH_SECONDS", "1.0"))


def _audit(p: Principal, request: Request):
    """Every data request is audited. Rows are queued and written in batches off the request path."""
    row = (p.tenant_id, p.email, f"API_{request.method}", json.dumps({"path": request.url.path, "query": str(request.url.query)}),
           request.headers.get("x-request-id"))
    try:
        _audit_q.put_nowait(row)
    except queue.Full:  # back-pressure: fall back to a synchronous write rather than dropping the record
        _write_audit([row])


def _write_audit(rows):
    from psycopg2.extras import execute_values

    with cursor(commit=True) as cur:
        execute_values(cur, "INSERT INTO audit_log (tenant_id, actor_type, actor_id, action, entity_type, metadata, request_id) VALUES %s",
                       rows, template="(%s, 'USER', %s, %s, 'api', %s, %s)")


def _audit_flusher():
    while True:
        rows = [_audit_q.get()]
        deadline = time.time() + AUDIT_FLUSH_SECONDS
        while len(rows) < 5000 and time.time() < deadline:
            try:
                rows.append(_audit_q.get(timeout=max(0.0, deadline - time.time())))
            except queue.Empty:
                break
        try:
            _write_audit(rows)
        except Exception:
            time.sleep(1)
            for r in rows:  # retry on the next cycle
                _audit_q.put(r)


def flush_audit():
    """Synchronously drain the queue (tests, shutdown)."""
    rows = []
    while True:
        try:
            rows.append(_audit_q.get_nowait())
        except queue.Empty:
            break
    if rows:
        _write_audit(rows)


threading.Thread(target=_audit_flusher, daemon=True, name="audit-flusher").start()
import atexit  # noqa: E402
atexit.register(flush_audit)


class LoginIn(BaseModel):
    email: str
    password: str


@app.post(f"{API}/auth/login")
def login(body: LoginIn, request: Request):
    _rate_limit(f"login:{request.client.host if request.client else 'unknown'}")
    with cursor() as cur:
        cur.execute("SELECT user_id::text, email, password_hash, role, tenant_id::text FROM app_user WHERE email = %s", (body.email.lower(),))
        u = cur.fetchone()
    if not u or not verify_password(body.password, u["password_hash"]):
        raise HTTPException(401, "invalid credentials")
    p = Principal(u["user_id"], u["email"], u["role"], u["tenant_id"])
    with cursor(commit=True) as cur:
        cur.execute("INSERT INTO audit_log (tenant_id, actor_type, actor_id, action, entity_type) VALUES (%s, 'USER', %s, 'LOGIN', 'session')",
                    (p.tenant_id, p.email))
    return {"access_token": issue_token(p), "token_type": "bearer", "role": p.role, "tenant_id": p.tenant_id}


@app.get(f"{API}/me")
def me(p: Principal = Depends(principal)):
    return {"user_id": p.user_id, "email": p.email, "role": p.role, "tenant_id": p.tenant_id}


# ----------------------------------------------------------------------------- helpers
def enc_cursor(values) -> str:
    return base64.urlsafe_b64encode(json.dumps(values, default=str).encode()).decode()


def dec_cursor(c: Optional[str]):
    if not c:
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(c.encode()))
    except Exception:
        raise HTTPException(400, "malformed cursor")


def page(rows, limit, key_fn):
    more = len(rows) > limit
    rows = rows[:limit]
    return {"items": rows, "next_cursor": enc_cursor(key_fn(rows[-1])) if more and rows else None}


def tenant(p: Principal) -> Optional[str]:
    return None if p.is_admin else p.tenant_id


# ----------------------------------------------------------------------------- endpoints
SUMMARY_TTL_SECONDS = int(os.getenv("SUMMARY_TTL_SECONDS", "2"))


@app.get(f"{API}/summary")
def summary(p: Principal = Depends(principal)):
    """Cached per tenant for a few seconds (cache-aside); the dashboard refreshes every 5 s."""
    key = f"cache:summary:{tenant(p) or 'all'}"
    try:
        hit = redis_client().get(key)
        if hit:
            return json.loads(hit)
    except Exception:
        hit = None
    with cursor() as cur:
        cur.execute(queries.SUMMARY, {"tenant": tenant(p)})
        s = cur.fetchone()
    try:
        redis_client().setex(key, SUMMARY_TTL_SECONDS, json.dumps(s, default=str))
    except Exception:
        pass
    return s


@app.get(f"{API}/priority")
def priority(p: Principal = Depends(principal), limit: int = Query(25, ge=1, le=200), cursor_: Optional[str] = Query(None, alias="cursor"),
             component: Optional[str] = Query(None, pattern="^(BRAKE|POWERTRAIN|BATTERY)$")):
    c = dec_cursor(cursor_)
    with cursor() as cur:
        cur.execute(queries.PRIORITY, {"tenant": tenant(p), "component": component, "limit": limit + 1,
                                       "c_loss": c[0] if c else None, "c_id": c[1] if c else None})
        rows = cur.fetchall()
    return page(rows, limit, lambda r: [r["expected_loss"], r["vehicle_component_id"]])


@app.get(f"{API}/alerts")
def alerts(p: Principal = Depends(principal), limit: int = Query(25, ge=1, le=200), cursor_: Optional[str] = Query(None, alias="cursor"),
           status: str = Query("ACTIVE", pattern="^(ACTIVE|ACKNOWLEDGED|RESOLVED)$"), source: Optional[str] = Query(None, pattern="^(ML|RULE)$")):
    c = dec_cursor(cursor_)
    with cursor() as cur:
        cur.execute(queries.ALERTS, {"tenant": tenant(p), "status": status, "source": source, "limit": limit + 1,
                                     "c_ts": c[0] if c else None, "c_id": c[1] if c else None})
        rows = cur.fetchall()
    return page(rows, limit, lambda r: [r["created_at"], r["alert_id"]])


@app.post(f"{API}/alerts/{{alert_id}}/acknowledge")
def acknowledge(alert_id: str, p: Principal = Depends(require("ADMIN", "FLEET_MANAGER"))):
    with cursor(commit=True) as cur:
        cur.execute(queries.ACK_ALERT, {"alert_id": alert_id, "tenant": tenant(p)})
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "active alert not found")
        cur.execute("INSERT INTO audit_log (tenant_id, actor_type, actor_id, action, entity_type, entity_id) VALUES (%s,'USER',%s,'ALERT_ACKNOWLEDGED','alert',%s)",
                    (p.tenant_id, p.email, alert_id))
    return row


@app.get(f"{API}/vehicles")
def vehicles(p: Principal = Depends(principal), limit: int = Query(50, ge=1, le=200), cursor_: Optional[str] = Query(None, alias="cursor"),
             q: Optional[str] = Query(None, max_length=17, pattern="^[A-HJ-NPR-Z0-9]*$")):
    c = dec_cursor(cursor_)
    with cursor() as cur:
        cur.execute(queries.VEHICLES, {"tenant": tenant(p), "limit": limit + 1, "c_id": c[0] if c else None,
                                       "q": f"{q}%" if q else None})
        rows = cur.fetchall()
    return page(rows, limit, lambda r: [r["vehicle_id"]])


@app.get(f"{API}/vehicles/{{vehicle_id}}")
def vehicle(vehicle_id: str, p: Principal = Depends(principal)):
    with cursor() as cur:
        cur.execute(queries.VEHICLE, {"vehicle_id": vehicle_id, "tenant": tenant(p)})
        v = cur.fetchone()
        if not v:
            raise HTTPException(404, "vehicle not found")
        cur.execute(queries.VEHICLE_COMPONENTS, {"vehicle_id": vehicle_id})
        v["components"] = cur.fetchall()
        cur.execute(queries.VEHICLE_EVENTS, {"vehicle_id": vehicle_id})
        v["maintenance_history"] = cur.fetchall()
    return v


@app.get(f"{API}/vehicles/{{vehicle_id}}/telemetry")
def telemetry(vehicle_id: str, p: Principal = Depends(principal), limit: int = Query(60, ge=1, le=500)):
    with cursor() as cur:
        cur.execute(queries.VEHICLE, {"vehicle_id": vehicle_id, "tenant": tenant(p)})
        if not cur.fetchone():
            raise HTTPException(404, "vehicle not found")
    with cursor(ts_pool) as cur:
        cur.execute(queries.TELEMETRY, {"vehicle_id": vehicle_id, "limit": limit})
        rows = cur.fetchall()
    if not p.is_admin:
        for r in rows:  # data minimisation: ~1 km location precision outside the admin role
            r["latitude"] = round(r["latitude"], 2) if r["latitude"] is not None else None
            r["longitude"] = round(r["longitude"], 2) if r["longitude"] is not None else None
    return {"items": rows}


@app.post(f"{API}/drivers/{{driver_id}}/erase")
def erase_driver(driver_id: str, p: Principal = Depends(require("ADMIN"))):
    """Right to erasure (GDPR / DPDP): removes the driver's personal fields and unlinks trips; keeps an audit row."""
    with cursor(commit=True) as cur:
        cur.execute("UPDATE driver SET name = NULL, license_number = NULL WHERE driver_id = %s RETURNING driver_id::text", (driver_id,))
        if not cur.fetchone():
            raise HTTPException(404, "driver not found")
        cur.execute("UPDATE trip SET driver_id = NULL WHERE driver_id = %s", (driver_id,))
        trips = cur.rowcount
        cur.execute("INSERT INTO audit_log (tenant_id, actor_type, actor_id, action, entity_type, entity_id, metadata) VALUES (%s,'USER',%s,'DRIVER_ERASED','driver',%s,%s)",
                    (p.tenant_id, p.email, driver_id, json.dumps({"trips_unlinked": trips})))
    return {"driver_id": driver_id, "erased": True, "trips_unlinked": trips}


@app.get(f"{API}/audit")
def audit(p: Principal = Depends(require("ADMIN")), limit: int = Query(50, ge=1, le=500), cursor_: Optional[str] = Query(None, alias="cursor")):
    c = dec_cursor(cursor_)
    with cursor() as cur:
        cur.execute(queries.AUDIT, {"limit": limit + 1, "c_ts": c[0] if c else None, "c_id": c[1] if c else None})
        rows = cur.fetchall()
    return page(rows, limit, lambda r: [r["occurred_at"], r["audit_id"]])


@app.get(f"{API}/risk/breakdown")
def risk_breakdown(p: Principal = Depends(principal)):
    """Per-component scored counts, alerts above threshold, expected loss and a P7d histogram."""
    with cursor() as cur:
        cur.execute(queries.RISK_BREAKDOWN, {"tenant": tenant(p)})
        rows = cur.fetchall()
    return {"items": rows}


@app.get(f"{API}/fleet/composition")
def fleet_composition(p: Principal = Depends(principal)):
    with cursor() as cur:
        cur.execute(queries.FLEET_COMPOSITION, {"tenant": tenant(p)})
        return {"items": cur.fetchall()}


MODEL_REPORT_PATH = os.getenv("MODEL_REPORT_PATH", "data/models/m1/report.json")


@app.get(f"{API}/models")
def model_report(p: Principal = Depends(principal)):
    """Held-out evaluation of the served models (produced by ml/train.py)."""
    try:
        with open(MODEL_REPORT_PATH, "r", encoding="utf-8") as f:
            r = json.load(f)
    except OSError:
        raise HTTPException(404, "model report not available")
    out = []
    for comp, c in r["components"].items():
        sel = c["selected"]
        out.append({
            "component": comp, "served_model": "Gradient boosting" if sel == "hgb" else "Logistic regression",
            "prevalence": c[sel]["test"]["prevalence"],
            "pr_auc_logreg": c["logreg"]["test"]["pr_auc"], "pr_auc_gbm": c["hgb"]["test"]["pr_auc"],
            "served": {k: c[sel]["test"].get(k) for k in ("pr_auc", "roc_auc", "brier", "precision", "recall", "precision_at_top_1%")},
            "holdout_pr_auc": c[sel]["test_vehicle_holdout"].get("pr_auc"),
            "features_used": c["features_used"], "rows": c["rows"],
            "top_features": c.get("hgb_top_features_permutation", [])[:5],
        })
    return {"model_version": r.get("model_version"), "dataset_version": r.get("dataset_version"),
            "feature_schema_version": r.get("feature_schema_version"), "components": out}


STREAM_METRICS_URL = os.getenv("STREAM_METRICS_URL", "http://localhost:8080/metrics")


@app.get(f"{API}/pipeline")
def pipeline(p: Principal = Depends(principal)):
    """Live stream-processor counters (dedup and feature engine) plus store health."""
    import urllib.request

    stream = None
    try:
        with urllib.request.urlopen(STREAM_METRICS_URL, timeout=2) as resp:  # nosec B310 - internal metrics URL from config
            stream = json.loads(resp.read())
    except Exception:
        stream = None
    stores = {}
    try:
        with cursor() as cur:
            cur.execute("SELECT 1")
        stores["postgres"] = "ok"
    except Exception:
        stores["postgres"] = "down"
    try:
        with cursor(ts_pool) as cur:
            cur.execute("SELECT 1")
        stores["timescaledb"] = "ok"
    except Exception:
        stores["timescaledb"] = "down"
    try:
        redis_client().ping()
        stores["redis"] = "ok"
    except Exception:
        stores["redis"] = "down"
    with cursor() as cur:
        cur.execute("SELECT max(scored_at) AS last_scored_at, count(*) AS scored FROM component_risk")
        risk = cur.fetchone()
    return {"stream_processor": stream, "stores": stores, "risk": risk, "server_time": time.time()}


class AskIn(BaseModel):
    question: str


@app.post(f"{API}/assistant")
def assistant_ask(body: AskIn, p: Principal = Depends(principal)):
    """Fleet assistant: LLM tool-calling over read-only, tenant-scoped tools (rules fallback)."""
    from . import assistant

    try:
        return assistant.ask(body.question, p)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/healthz", include_in_schema=False)
def healthz():
    with cursor() as cur:
        cur.execute("SELECT 1 AS ok")
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
