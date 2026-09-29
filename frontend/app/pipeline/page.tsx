"use client"

import { useEffect, useRef, useState } from "react"
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"
import { ArrowRight } from "lucide-react"
import { DashboardLayout } from "@/components/dashboard/dashboard-layout"
import { ErrorLine, PageHeader, Panel } from "@/components/dashboard/fleet-ui"
import { usePoll } from "@/hooks/use-poll"
import { api } from "@/lib/api"

interface Pipeline {
  stream_processor: null | { accepted: number; duplicates_dropped: number; bloom_fast_path: number; redis_reads: number; bloom_trusted: boolean
    features?: { events_applied: number; duplicates_skipped: number; late_events_ignored: number; snapshots_written: number; snapshot_latency_ms_p50: number | null; snapshot_latency_ms_p95: number | null } }
  stores: Record<string, string>; risk: { last_scored_at: string | null; scored: number }; server_time: number
}

const FLOW = ["Vehicles (OEM-A / OEM-B)", "Kafka oem.inbound", "Identity resolver (VIN check, DLQ)", "Normalizer (adapter per OEM)", "Kafka vehicle.normalized", "Stream processor (dedup, features)", "Risk scorer (calibrated models)", "API + dashboard"]

function PipelineInner() {
  const { data, error } = usePoll(() => api<Pipeline>("/pipeline"), 2000)
  const [series, setSeries] = useState<{ t: string; rate: number }[]>([])
  const prev = useRef<{ n: number; t: number } | null>(null)

  useEffect(() => {
    const sp = data?.stream_processor
    if (!sp) return
    const now = { n: sp.accepted + sp.duplicates_dropped, t: data!.server_time }
    if (prev.current && now.t > prev.current.t) {
      const rate = Math.max(0, (now.n - prev.current.n) / (now.t - prev.current.t))
      setSeries((s) => [...s.slice(-59), { t: new Date(now.t * 1000).toLocaleTimeString(), rate: Math.round(rate) }])
    }
    prev.current = now
  }, [data])

  const sp = data?.stream_processor
  const f = sp?.features
  const tiles: [string, string][] = sp ? [
    ["Events accepted", sp.accepted.toLocaleString()],
    ["Duplicates dropped", sp.duplicates_dropped.toLocaleString()],
    ["Feature updates applied", (f?.events_applied ?? 0).toLocaleString()],
    ["Snapshots written", (f?.snapshots_written ?? 0).toLocaleString()],
    ["Late events ignored", (f?.late_events_ignored ?? 0).toLocaleString()],
    ["Snapshot latency p95", f?.snapshot_latency_ms_p95 != null ? `${Math.round(f.snapshot_latency_ms_p95)} ms` : "-"],
  ] : []

  return (
    <div className="p-4 md:p-6 lg:p-8">
      <PageHeader eyebrow="Live system" title="Pipeline" subtitle="Kafka at-least-once delivery with idempotent consumers: duplicates are dropped, never double counted." />
      <ErrorLine error={error} />
      <Panel title="Event path" className="mb-6">
        <div className="flex flex-wrap items-center gap-2">
          {FLOW.map((s, i) => (
            <div key={s} className="flex items-center gap-2">
              <span className="text-xs font-mono border border-border px-2 py-1.5 bg-surface">{s}</span>
              {i < FLOW.length - 1 && <ArrowRight className="w-3 h-3 text-lime" />}
            </div>
          ))}
        </div>
        <p className="text-[11px] text-muted-foreground mt-3">A separate rule engine also consumes vehicle.normalized and raises DTC alerts in milliseconds, independent of dedup and ML.</p>
      </Panel>
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3 md:gap-4 mb-6">
        {tiles.map(([k, v]) => (
          <div key={k} className="bg-card border border-border p-4"><p className="text-xs text-muted-foreground mb-1">{k}</p><p className="text-xl font-mono font-semibold">{v}</p></div>
        ))}
        {!sp && <p className="text-sm text-muted-foreground col-span-full">Stream processor metrics unavailable.</p>}
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 md:gap-6">
        <Panel title="Stream-processor throughput (events/s, live)" className="xl:col-span-2">
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={series}>
                <defs><linearGradient id="thr" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="var(--lime)" stopOpacity={0.4} /><stop offset="100%" stopColor="var(--card)" stopOpacity={0} /></linearGradient></defs>
                <XAxis dataKey="t" tick={{ fill: "var(--muted-foreground)", fontSize: 10 }} minTickGap={40} />
                <YAxis tick={{ fill: "var(--muted-foreground)", fontSize: 10 }} width={40} />
                <Tooltip contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", fontSize: 12 }} />
                <Area type="monotone" dataKey="rate" stroke="var(--lime)" fill="url(#thr)" isAnimationActive={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Panel>
        <Panel title="Stores and scoring">
          <div className="space-y-2 text-sm">
            {Object.entries(data?.stores || {}).map(([k, v]) => (
              <div key={k} className="flex justify-between border-b border-border pb-2"><span className="font-mono">{k}</span><span className={v === "ok" ? "text-lime font-mono" : "text-destructive font-mono"}>{v}</span></div>
            ))}
            <div className="flex justify-between border-b border-border pb-2"><span className="font-mono">bloom filter</span><span className="font-mono text-muted-foreground">{sp ? (sp.bloom_trusted ? "trusted" : "warming up") : "-"}</span></div>
            <div className="flex justify-between border-b border-border pb-2"><span className="font-mono">components scored</span><span className="font-mono">{data?.risk.scored.toLocaleString() ?? "-"}</span></div>
            <div className="flex justify-between"><span className="font-mono">last scored</span><span className="font-mono text-muted-foreground">{data?.risk.last_scored_at ? new Date(data.risk.last_scored_at).toLocaleTimeString() : "-"}</span></div>
          </div>
        </Panel>
      </div>
    </div>
  )
}

export default function Page() { return <DashboardLayout><PipelineInner /></DashboardLayout> }
