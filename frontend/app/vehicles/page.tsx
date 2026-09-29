"use client"

import { useState } from "react"
import Link from "next/link"
import { DashboardLayout } from "@/components/dashboard/dashboard-layout"
import { ErrorLine, PageHeader, Panel } from "@/components/dashboard/fleet-ui"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { usePoll } from "@/hooks/use-poll"
import { api, fmtPct, type Page, type VehicleRow } from "@/lib/api"

function VehiclesInner() {
  const [q, setQ] = useState("")
  const [stack, setStack] = useState<(string | null)[]>([null])
  const cursor = stack[stack.length - 1]
  const vin = q.trim().toUpperCase().replace(/[^A-HJ-NPR-Z0-9]/g, "")
  const params = new URLSearchParams({ limit: "50", ...(cursor ? { cursor } : {}), ...(vin ? { q: vin } : {}) })
  const { data, error } = usePoll(() => api<Page<VehicleRow>>(`/vehicles?${params}`), 0, [cursor, vin])

  return (
    <div className="p-4 md:p-6 lg:p-8">
      <PageHeader eyebrow="Fleet registry" title="Vehicles" subtitle="Every connected vehicle in your tenant. Search by VIN prefix." />
      <ErrorLine error={error} />
      <Panel title="Vehicles" right={<Input placeholder="VIN prefix, e.g. 5YJC" value={q} onChange={(e) => { setQ(e.target.value); setStack([null]) }} className="w-56 h-8 font-mono" />}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr className="text-left text-xs text-muted-foreground font-mono uppercase tracking-wider">
              <th className="py-2 pr-3">VIN</th><th className="py-2 pr-3">Type</th><th className="py-2 pr-3">Make / model</th><th className="py-2 pr-3">Year</th><th className="py-2 pr-3">Odometer</th><th className="py-2 pr-3">Fleet</th><th className="py-2 text-right">Max P(7d)</th>
            </tr></thead>
            <tbody>
              {(data?.items || []).map((v) => (
                <tr key={v.vehicle_id} className="border-t border-border hover:bg-surface-hover">
                  <td className="py-2 pr-3 font-mono"><Link href={`/vehicles/${v.vehicle_id}`} className="hover:text-lime">{v.vin}</Link></td>
                  <td className="py-2 pr-3 text-muted-foreground">{v.vehicle_type}</td>
                  <td className="py-2 pr-3">{v.make} {v.model}</td>
                  <td className="py-2 pr-3 font-mono text-muted-foreground">{v.year}</td>
                  <td className="py-2 pr-3 font-mono text-muted-foreground">{v.odometer_km?.toLocaleString()} km</td>
                  <td className="py-2 pr-3 text-muted-foreground">{v.fleet}</td>
                  <td className="py-2 text-right font-mono">{fmtPct(v.max_p7d)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="flex gap-2 mt-4">
          <Button variant="outline" size="sm" className="bg-transparent" disabled={stack.length === 1} onClick={() => setStack((s) => s.slice(0, -1))}>Previous</Button>
          <Button variant="outline" size="sm" className="bg-transparent" disabled={!data?.next_cursor} onClick={() => data?.next_cursor && setStack((s) => [...s, data.next_cursor])}>Next</Button>
        </div>
      </Panel>
    </div>
  )
}

export default function Page() { return <DashboardLayout><VehiclesInner /></DashboardLayout> }
