"use client"

import { useState } from "react"
import Link from "next/link"
import { toast } from "sonner"
import { DashboardLayout } from "@/components/dashboard/dashboard-layout"
import { ComponentTag, ErrorLine, PageHeader, Panel, SeverityBadge } from "@/components/dashboard/fleet-ui"
import { Button } from "@/components/ui/button"
import { usePoll } from "@/hooks/use-poll"
import { useAuth } from "@/context/auth-context"
import { api, fmtPct, type Alert, type Page } from "@/lib/api"

function AlertsInner() {
  const { me } = useAuth()
  const [source, setSource] = useState("")
  const [status, setStatus] = useState("ACTIVE")
  const q = new URLSearchParams({ limit: "50", status, ...(source ? { source } : {}) })
  const { data, error, reload } = usePoll(() => api<Page<Alert>>(`/alerts?${q}`), 2000, [source, status])
  const canAck = me?.role === "ADMIN" || me?.role === "FLEET_MANAGER"

  const ack = async (id: string) => {
    try { await api(`/alerts/${id}/acknowledge`, { method: "POST" }); toast.success("Alert acknowledged (audited)"); reload() }
    catch (e) { toast.error(e instanceof Error ? e.message : "Failed") }
  }

  return (
    <div className="p-4 md:p-6 lg:p-8">
      <PageHeader eyebrow="Live, refreshes every 2 s" title="Alerts"
        subtitle="RULE alerts come from DTC fault codes within seconds; ML alerts fire when a component's calibrated 7-day risk crosses its threshold." />
      <ErrorLine error={error} />
      <Panel title={`${data?.items.length ?? 0} alerts`} right={
        <div className="flex gap-1 flex-wrap">
          {["", "ML", "RULE"].map((s) => (
            <button key={s || "all"} onClick={() => setSource(s)} className={`text-xs font-mono px-2 py-1 border border-border ${source === s ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-surface-hover"}`}>{s || "ALL SOURCES"}</button>
          ))}
          {["ACTIVE", "ACKNOWLEDGED", "RESOLVED"].map((s) => (
            <button key={s} onClick={() => setStatus(s)} className={`text-xs font-mono px-2 py-1 border border-border ${status === s ? "bg-secondary text-foreground" : "text-muted-foreground hover:bg-surface-hover"}`}>{s}</button>
          ))}
        </div>}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr className="text-left text-xs text-muted-foreground font-mono uppercase tracking-wider">
              <th className="py-2 pr-3">Time</th><th className="py-2 pr-3">VIN</th><th className="py-2 pr-3">Component</th><th className="py-2 pr-3">Alert</th><th className="py-2 pr-3">Severity</th><th className="py-2 pr-3">Source</th><th className="py-2"></th>
            </tr></thead>
            <tbody>
              {(data?.items || []).map((a) => (
                <tr key={a.alert_id} className="border-t border-border hover:bg-surface-hover">
                  <td className="py-2 pr-3 font-mono text-xs text-muted-foreground whitespace-nowrap">{new Date(a.created_at).toLocaleString()}</td>
                  <td className="py-2 pr-3 font-mono"><Link href={`/vehicles/${a.vehicle_id}`} className="hover:text-lime">{a.vin}</Link></td>
                  <td className="py-2 pr-3"><ComponentTag component={a.component} /></td>
                  <td className="py-2 pr-3" title={a.message}>{a.source === "ML" ? `7-day risk ${fmtPct(a.risk_probability)}` : a.alert_type}</td>
                  <td className="py-2 pr-3"><SeverityBadge severity={a.severity} /></td>
                  <td className="py-2 pr-3 font-mono text-xs text-muted-foreground">{a.source}</td>
                  <td className="py-2 text-right">{status === "ACTIVE" && canAck && <Button size="sm" variant="outline" className="bg-transparent h-7 text-xs" onClick={() => ack(a.alert_id)}>Acknowledge</Button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {canAck && status !== "ACTIVE" && <p className="text-[11px] text-muted-foreground mt-3">Only ACTIVE alerts can be acknowledged. Switch the filter to ACTIVE.</p>}
        {!canAck && <p className="text-[11px] text-muted-foreground mt-3">Your role ({me?.role}) can view alerts but not acknowledge them.</p>}
      </Panel>
    </div>
  )
}

export default function Page() { return <DashboardLayout><AlertsInner /></DashboardLayout> }
