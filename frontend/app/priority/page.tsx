"use client"

import { useState } from "react"
import { DashboardLayout } from "@/components/dashboard/dashboard-layout"
import { ComponentTag, ErrorLine, PageHeader, Panel, RiskBar } from "@/components/dashboard/fleet-ui"
import { Button } from "@/components/ui/button"
import { usePoll } from "@/hooks/use-poll"
import { api, fmtMoney, type Page, type PriorityItem } from "@/lib/api"

const PAGE = 25

function PriorityInner() {
  const [component, setComponent] = useState("")
  const [stack, setStack] = useState<(string | null)[]>([null])
  const cursor = stack[stack.length - 1]
  const q = new URLSearchParams({ limit: String(PAGE), ...(cursor ? { cursor } : {}), ...(component ? { component } : {}) })
  const { data, error } = usePoll(() => api<Page<PriorityItem>>(`/priority?${q}`), 5000, [cursor, component])
  const offset = (stack.length - 1) * PAGE

  return (
    <div className="p-4 md:p-6 lg:p-8">
      <PageHeader eyebrow="Maintenance queue" title="Priority"
        subtitle="Expected loss = P(7d) x (repair cost + downtime hours x hourly cost). Missing cost data is flagged, never zero." />
      <ErrorLine error={error} />
      <Panel title={`Ranked components${component ? `: ${component}` : ""}`} right={
        <div className="flex gap-1">
          {["", "BRAKE", "POWERTRAIN", "BATTERY"].map((c) => (
            <button key={c || "all"} onClick={() => { setComponent(c); setStack([null]) }}
              className={`text-xs font-mono px-2 py-1 border border-border ${component === c ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-surface-hover"}`}>{c || "ALL"}</button>
          ))}
        </div>}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr className="text-left text-xs text-muted-foreground font-mono uppercase tracking-wider">
              <th className="py-2 pr-3">#</th><th className="py-2 pr-3">VIN</th><th className="py-2 pr-3">Type</th><th className="py-2 pr-3">Component</th>
              <th className="py-2 pr-3">P(7d)</th><th className="py-2 pr-3">Threshold</th><th className="py-2 pr-3">Snapshot</th><th className="py-2 text-right">Expected loss</th>
            </tr></thead>
            <tbody>
              {(data?.items || []).map((r, i) => (
                <tr key={r.vehicle_component_id} className="border-t border-border hover:bg-surface-hover cursor-pointer" onClick={() => (window.location.href = `/vehicles/${r.vehicle_id}`)}>
                  <td className="py-2 pr-3 font-mono text-muted-foreground">{offset + i + 1}</td>
                  <td className="py-2 pr-3 font-mono">{r.vin}</td>
                  <td className="py-2 pr-3 text-muted-foreground">{r.vehicle_type}</td>
                  <td className="py-2 pr-3"><ComponentTag component={r.component} /></td>
                  <td className="py-2 pr-3"><RiskBar p={r.p7d} threshold={r.threshold} /></td>
                  <td className="py-2 pr-3 font-mono text-xs text-muted-foreground">{(100 * r.threshold).toFixed(1)}%</td>
                  <td className="py-2 pr-3 font-mono text-xs text-muted-foreground">{new Date(r.feature_ts).toISOString().slice(0, 16).replace("T", " ")}</td>
                  <td className="py-2 text-right font-mono">{fmtMoney(r.expected_loss)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="flex gap-2 mt-4">
          <Button variant="outline" size="sm" className="bg-transparent" disabled={stack.length === 1} onClick={() => setStack((s) => s.slice(0, -1))}>Previous</Button>
          <Button variant="outline" size="sm" className="bg-transparent" disabled={!data?.next_cursor} onClick={() => data?.next_cursor && setStack((s) => [...s, data.next_cursor])}>Next</Button>
          <span className="text-xs font-mono text-muted-foreground self-center ml-2">page {stack.length} (keyset pagination)</span>
        </div>
      </Panel>
    </div>
  )
}

export default function Page() { return <DashboardLayout><PriorityInner /></DashboardLayout> }
