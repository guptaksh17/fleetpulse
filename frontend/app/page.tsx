"use client"

import Link from "next/link"
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"
import { DashboardLayout } from "@/components/dashboard/dashboard-layout"
import { GlanceCard } from "@/components/dashboard/glance-card"
import { ComponentTag, ErrorLine, PageHeader, Panel, RiskBar, SeverityBadge } from "@/components/dashboard/fleet-ui"
import { usePoll } from "@/hooks/use-poll"
import { api, fmtCompact, fmtMoney, fmtPct, type Alert, type Page, type PriorityItem, type RiskBreakdown, type Summary } from "@/lib/api"

const BUCKETS: [keyof RiskBreakdown, string, string][] = [
  ["b_0_5", "< 5%", "var(--chart-1)"],
  ["b_5_10", "5-10%", "var(--chart-2)"],
  ["b_10_25", "10-25%", "var(--chart-4)"],
  ["b_25_50", "25-50%", "var(--chart-5)"],
  ["b_50_100", ">= 50%", "var(--destructive)"],
]
const money = (n: number) => `$${fmtCompact(n)}`

function PulseInner() {
  const summary = usePoll(() => api<Summary>("/summary"), 2000)
  const breakdown = usePoll(() => api<{ items: RiskBreakdown[] }>("/risk/breakdown"), 10000)
  const top = usePoll(() => api<Page<PriorityItem>>("/priority?limit=8"), 5000)
  const alerts = usePoll(() => api<Page<Alert>>("/alerts?limit=8"), 2000)
  const composition = usePoll(() => api<{ items: { vehicle_type: string; oem_id: string; vehicles: number }[] }>("/fleet/composition"), 30000)
  const s = summary.data

  const riskChart = (breakdown.data?.items || []).map((r) => ({ component: r.component, ...Object.fromEntries(BUCKETS.map(([k]) => [k, r[k]])) }))
  const lossChart = (breakdown.data?.items || []).map((r) => ({ component: r.component, loss: r.expected_loss, alerts: r.above_threshold }))
  const comp = composition.data?.items || []
  const types = Array.from(new Set(comp.map((c) => c.vehicle_type)))
  const oems = Array.from(new Set(comp.map((c) => c.oem_id)))
  const compChart = types.map((t) => ({ type: t, ...Object.fromEntries(oems.map((o) => [o, comp.find((c) => c.vehicle_type === t && c.oem_id === o)?.vehicles ?? 0])) }))

  return (
    <div className="p-4 md:p-6 lg:p-8">
      <PageHeader eyebrow="Real-time overview" title="Fleet Pulse"
        subtitle="Calibrated probability that each component needs maintenance or fails in the next 7 days, priced in dollars"
        right={<span className="text-xs font-mono text-muted-foreground hidden sm:block">{summary.updatedAt ? `updated ${summary.updatedAt.toLocaleTimeString()}` : "loading..."}</span>} />
      <ErrorLine error={summary.error} />

      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3 md:gap-4 mb-6 md:mb-8">
        <GlanceCard title="Vehicles" value={s?.vehicles ?? 0} caption="in your fleet" />
        <GlanceCard title="Components scored" value={s?.components_scored ?? 0} caption="brake, powertrain, battery" />
        <GlanceCard title="Above threshold" value={s?.components_above_threshold ?? 0} caption="need attention" />
        <GlanceCard title="Expected loss, 7 days" value={s?.total_expected_loss_7d ?? 0} format={money} caption="P(7d) x cost" />
        <GlanceCard title="Active ML alerts" value={s?.active_ml_alerts ?? 0} caption="risk above threshold" />
        <GlanceCard title="Active rule alerts" value={s?.active_rule_alerts ?? 0} caption="DTC faults" />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 md:gap-6 mb-6">
        <Panel title="Risk distribution by component (components per 7-day probability band)" className="xl:col-span-2">
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={riskChart} layout="vertical" margin={{ left: 20 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" horizontal={false} />
                <XAxis type="number" tick={{ fill: "var(--muted-foreground)", fontSize: 11 }} scale="sqrt" tickFormatter={fmtCompact} />
                <YAxis type="category" dataKey="component" tick={{ fill: "var(--muted-foreground)", fontSize: 11 }} width={90} />
                <Tooltip contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", fontSize: 12 }} formatter={(v: number) => v.toLocaleString()} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                {BUCKETS.map(([k, label, color]) => <Bar key={k} dataKey={k} name={label} stackId="r" fill={color} />)}
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="text-[11px] text-muted-foreground mt-2">Square-root scale: most components are healthy; the long tail on the right is what the queue prioritises.</p>
        </Panel>
        <Panel title="Expected 7-day loss by component">
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={lossChart}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                <XAxis dataKey="component" tick={{ fill: "var(--muted-foreground)", fontSize: 11 }} />
                <YAxis tick={{ fill: "var(--muted-foreground)", fontSize: 11 }} tickFormatter={money} width={60} />
                <Tooltip contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", fontSize: 12 }} formatter={(v: number, n: string) => (n === "loss" ? fmtMoney(v) : v)} />
                <Bar dataKey="loss" name="loss" fill="var(--lime)" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 md:gap-6">
        <Panel title="Service first (highest 7-day expected loss)" className="xl:col-span-2"
          right={<Link href="/priority" className="text-xs font-mono text-lime hover:underline">full queue</Link>}>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="text-left text-xs text-muted-foreground font-mono uppercase tracking-wider">
                <th className="py-2 pr-3">#</th><th className="py-2 pr-3">VIN</th><th className="py-2 pr-3">Type</th><th className="py-2 pr-3">Component</th><th className="py-2 pr-3">P(7d)</th><th className="py-2 text-right">Expected loss</th>
              </tr></thead>
              <tbody>
                {(top.data?.items || []).map((r, i) => (
                  <tr key={r.vehicle_component_id} className="border-t border-border hover:bg-surface-hover cursor-pointer" onClick={() => (window.location.href = `/vehicles/${r.vehicle_id}`)}>
                    <td className="py-2 pr-3 font-mono text-muted-foreground">{i + 1}</td>
                    <td className="py-2 pr-3 font-mono">{r.vin}</td>
                    <td className="py-2 pr-3 text-muted-foreground">{r.vehicle_type}</td>
                    <td className="py-2 pr-3"><ComponentTag component={r.component} /></td>
                    <td className="py-2 pr-3"><RiskBar p={r.p7d} threshold={r.threshold} /></td>
                    <td className="py-2 text-right font-mono">{fmtMoney(r.expected_loss)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>

        <div className="flex flex-col gap-4 md:gap-6">
          <Panel title="Live alerts" right={<Link href="/alerts" className="text-xs font-mono text-lime hover:underline">all</Link>}>
            <div className="space-y-2">
              {(alerts.data?.items || []).map((a) => (
                <Link key={a.alert_id} href={`/vehicles/${a.vehicle_id}`} className="flex items-center gap-3 p-2 border border-border hover:bg-surface-hover transition-colors">
                  <SeverityBadge severity={a.severity} />
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-mono truncate">{a.vin}</p>
                    <p className="text-xs text-muted-foreground truncate">{a.source === "ML" ? `${a.component} risk ${fmtPct(a.risk_probability)}` : a.alert_type}</p>
                  </div>
                  <span className="text-[10px] font-mono text-muted-foreground">{new Date(a.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                </Link>
              ))}
              {alerts.data && alerts.data.items.length === 0 && <p className="text-xs text-muted-foreground">No active alerts.</p>}
            </div>
          </Panel>
          <Panel title="Fleet composition (type x OEM)">
            <div className="h-40">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={compChart}>
                  <XAxis dataKey="type" tick={{ fill: "var(--muted-foreground)", fontSize: 11 }} />
                  <YAxis tick={{ fill: "var(--muted-foreground)", fontSize: 11 }} tickFormatter={fmtCompact} width={40} />
                  <Tooltip contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", fontSize: 12 }} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  {oems.map((o, i) => <Bar key={o} dataKey={o} stackId="o" fill={i === 0 ? "var(--chart-1)" : "var(--chart-2)"} />)}
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Panel>
        </div>
      </div>
    </div>
  )
}

export default function Page() {
  return <DashboardLayout><PulseInner /></DashboardLayout>
}
