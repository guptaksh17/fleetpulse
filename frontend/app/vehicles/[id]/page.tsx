"use client"

import { use } from "react"
import Link from "next/link"
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"
import { Bot } from "lucide-react"
import { DashboardLayout } from "@/components/dashboard/dashboard-layout"
import { ComponentTag, ErrorLine, PageHeader, Panel, RiskBar } from "@/components/dashboard/fleet-ui"
import { usePoll } from "@/hooks/use-poll"
import { api, fmtMoney, fmtPct } from "@/lib/api"

interface VehicleDetail {
  vehicle_id: string; vin: string; vehicle_type: string; make: string; model: string; year: number; odometer_km: number; fleet: string
  components: { component: string; status: string; p7d: number | null; threshold: number | null; expected_loss: number | null; cost_status: string | null; model_version: string | null; feature_ts: string | null }[]
  maintenance_history: { event_type: string; occurred_at: string; odometer_km: number | null; component: string }[]
}
type Row = Record<string, number | string | null>

function Trend({ rows, keyName, label, unit, color }: { rows: Row[]; keyName: string; label: string; unit: string; color: string }) {
  const data = rows.filter((r) => r[keyName] != null).map((r) => ({ t: String(r.event_ts).slice(11, 16), v: Number(r[keyName]) }))
  if (data.length < 2) return null
  return (
    <div>
      <p className="text-xs font-mono text-muted-foreground uppercase tracking-wider mb-1">{label} ({unit})</p>
      <div className="h-32">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis dataKey="t" tick={{ fill: "var(--muted-foreground)", fontSize: 10 }} minTickGap={30} />
            <YAxis tick={{ fill: "var(--muted-foreground)", fontSize: 10 }} width={40} domain={["auto", "auto"]} />
            <Tooltip contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", fontSize: 12 }} />
            <Line type="monotone" dataKey="v" stroke={color} strokeWidth={1.5} dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

function VehicleInner({ id }: { id: string }) {
  const v = usePoll(() => api<VehicleDetail>(`/vehicles/${id}`), 5000, [id])
  const t = usePoll(() => api<{ items: Row[] }>(`/vehicles/${id}/telemetry?limit=200`), 5000, [id])
  const d = v.data
  const rows = (t.data?.items || []).slice().reverse()
  const ev = d?.vehicle_type !== "ICE"

  return (
    <div className="p-4 md:p-6 lg:p-8">
      <PageHeader eyebrow={d ? `${d.vehicle_type} · ${d.fleet}` : "vehicle"} title={d ? d.vin : "Loading..."}
        subtitle={d ? `${d.make} ${d.model} (${d.year}) · odometer ${d.odometer_km?.toLocaleString()} km` : undefined}
        right={d && <Link href={`/assistant?q=${encodeURIComponent(`Tell me about ${d.vin}`)}`} className="flex items-center gap-2 text-xs font-mono border border-border px-3 py-2 hover:bg-surface-hover"><Bot className="w-4 h-4 text-lime" /> Ask the assistant</Link>} />
      <ErrorLine error={v.error} />

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        {(d?.components || []).map((c) => (
          <div key={c.component} className={`bg-card border p-5 ${c.p7d != null && c.threshold != null && c.p7d >= c.threshold ? "border-destructive/50" : "border-border"}`}>
            <div className="flex items-center justify-between mb-3"><ComponentTag component={c.component} /><span className="text-[10px] font-mono text-muted-foreground">{c.status}</span></div>
            {c.status === "NOT_APPLICABLE" ? <>
              <p className="text-3xl font-mono font-semibold tracking-tighter mb-1 text-muted-foreground">n/a</p>
              <p className="text-xs text-muted-foreground mb-3">this vehicle type has no {c.component.toLowerCase()} to score</p>
            </> : <>
            <p className="text-3xl font-mono font-semibold tracking-tighter mb-1">{fmtPct(c.p7d)}</p>
            <p className="text-xs text-muted-foreground mb-3">probability of maintenance or failure in 7 days</p>
            {c.p7d != null && c.threshold != null && <RiskBar p={c.p7d} threshold={c.threshold} />}
            <div className="mt-3 flex justify-between text-xs font-mono"><span className="text-muted-foreground">expected loss</span><span>{fmtMoney(c.expected_loss)}</span></div>
            <div className="mt-1 flex justify-between text-xs font-mono"><span className="text-muted-foreground">alert threshold</span><span>{c.threshold != null ? fmtPct(c.threshold) : "-"}</span></div>
            </>}
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 md:gap-6">
        <Panel title="Recent telemetry (last 200 events)" className="xl:col-span-2">
          <ErrorLine error={t.error} />
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Trend rows={rows} keyName="speed_kmh" label="Speed" unit="km/h" color="var(--chart-1)" />
            {ev ? <Trend rows={rows} keyName="motor_temp_c" label="Motor temperature" unit="C" color="var(--chart-5)" />
                : <Trend rows={rows} keyName="engine_temp_c" label="Engine temperature" unit="C" color="var(--chart-5)" />}
            {ev && <Trend rows={rows} keyName="voltage_v" label="Pack voltage" unit="V" color="var(--chart-2)" />}
            {ev && <Trend rows={rows} keyName="soc_pct" label="State of charge" unit="%" color="var(--chart-4)" />}
            {!ev && <Trend rows={rows} keyName="rpm" label="Engine speed" unit="rpm" color="var(--chart-2)" />}
          </div>
          <p className="text-[11px] text-muted-foreground mt-3">Location is rounded to about 1 km for non-admin roles (data minimisation).</p>
        </Panel>
        <Panel title="Service history">
          <div className="space-y-2">
            {(d?.maintenance_history || []).slice(0, 12).map((e, i) => (
              <div key={i} className="flex items-center gap-3 text-xs border-b border-border pb-2">
                <span className={`font-mono ${e.event_type === "SERVICE_COMPLETED" ? "text-lime" : e.event_type === "FAILURE" ? "text-destructive" : "text-amber-400"}`}>{e.event_type.replace("_", " ")}</span>
                <ComponentTag component={e.component} />
                <span className="ml-auto font-mono text-muted-foreground">{new Date(e.occurred_at).toISOString().slice(0, 10)}</span>
              </div>
            ))}
            {d && d.maintenance_history.length === 0 && <p className="text-xs text-muted-foreground">No service records.</p>}
          </div>
        </Panel>
      </div>
    </div>
  )
}

export default function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  return <DashboardLayout><VehicleInner id={id} /></DashboardLayout>
}
