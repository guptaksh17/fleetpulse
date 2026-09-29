// Typed client for the FleetPulse REST API (proxied under /api/v1 by next.config.mjs).

export type Role = "ADMIN" | "FLEET_MANAGER" | "VIEWER"
export interface Me { user_id: string; email: string; role: Role; tenant_id: string | null }
export interface Summary {
  vehicles: number; components_scored: number; components_above_threshold: number
  total_expected_loss_7d: number; active_ml_alerts: number; active_rule_alerts: number; last_scored_at: string | null
}
export interface PriorityItem {
  vehicle_component_id: string; vehicle_id: string; vin: string; vehicle_type: string; component: string
  p7d: number; threshold: number; expected_loss: number; cost_status: string; feature_ts: string; model_version: string
}
export interface Alert {
  alert_id: string; alert_type: string; severity: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"; source: "ML" | "RULE"
  status: string; risk_probability: number | null; message: string; created_at: string; component: string
  vehicle_id: string; vin: string; model_name: string | null
}
export interface VehicleRow { vehicle_id: string; vin: string; vehicle_type: string; make: string; model: string; year: number; odometer_km: number; fleet: string; max_p7d: number | null }
export interface Page<T> { items: T[]; next_cursor: string | null }
export interface RiskBreakdown { component: string; scored: number; above_threshold: number; expected_loss: number; mean_p7d: number; b_0_5: number; b_5_10: number; b_10_25: number; b_25_50: number; b_50_100: number }
export interface AssistantReply { answer: string; mode: string; tools_used: { tool: string; args: Record<string, unknown> }[]; latency_ms: number; llm_error?: string }

const TOKEN_KEY = "fp_token"

// Local development only: accept a token from the URL fragment (never sent to the server) for
// scripted screenshots. Ignored on any host other than localhost.
if (typeof window !== "undefined" && ["localhost", "127.0.0.1"].includes(window.location.hostname) && window.location.hash.startsWith("#token=")) {
  try { window.sessionStorage.setItem(TOKEN_KEY, decodeURIComponent(window.location.hash.slice(7))) } catch { /* storage unavailable */ }
  window.history.replaceState(null, "", window.location.pathname + window.location.search)
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null
  try { return window.sessionStorage.getItem(TOKEN_KEY) } catch { return null }
}
export function setToken(t: string | null) {
  try { t ? window.sessionStorage.setItem(TOKEN_KEY, t) : window.sessionStorage.removeItem(TOKEN_KEY) } catch { /* storage unavailable */ }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken()
  const res = await fetch(`/api/v1${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(init.headers || {}) },
    cache: "no-store",
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    if (res.status === 401 && typeof window !== "undefined") {
      setToken(null)
      if (!window.location.pathname.startsWith("/login")) window.location.href = "/login"
    }
    throw new ApiError(res.status, body?.error?.message || res.statusText)
  }
  return body as T
}

export async function login(email: string, password: string): Promise<void> {
  const r = await api<{ access_token: string }>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) })
  setToken(r.access_token)
}

export const fmtPct = (p: number | null | undefined) => (p == null ? "-" : p >= 0.999 ? ">99.9%" : `${(100 * p).toFixed(1)}%`)
export const fmtMoney = (v: number | null | undefined) => (v == null || v < 0 ? "cost data required" : `$${Math.round(v).toLocaleString()}`)
export const fmtCompact = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : n >= 1e4 ? `${(n / 1e3).toFixed(1)}K` : n.toLocaleString())
