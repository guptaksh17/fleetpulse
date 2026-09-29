"use client"

import React from "react"
import { cn } from "@/lib/utils"
import { fmtPct } from "@/lib/api"

export function PageHeader({ eyebrow, title, subtitle, right }: { eyebrow: string; title: string; subtitle?: string; right?: React.ReactNode }) {
  return (
    <div className="mb-6 md:mb-8 flex items-start justify-between gap-4">
      <div>
        <div className="flex items-center gap-3 mb-2">
          <div className="w-2 h-2 bg-lime pulse-live" />
          <span className="text-xs font-mono text-muted-foreground uppercase tracking-wider">{eyebrow}</span>
        </div>
        <h1 className="text-2xl md:text-3xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="text-sm md:text-base text-muted-foreground mt-1">{subtitle}</p>}
      </div>
      {right}
    </div>
  )
}

export function Panel({ title, right, children, className }: { title: string; right?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={cn("bg-card border border-border p-4 md:p-6", className)}>
      <div className="flex items-center justify-between mb-4 gap-2">
        <h2 className="text-sm font-medium">{title}</h2>
        {right}
      </div>
      {children}
    </section>
  )
}

/** Risk relative to the component's alert threshold: lime below half, amber approaching, red above. */
export function RiskBar({ p, threshold }: { p: number; threshold: number }) {
  const width = Math.min(100, (100 * p) / Math.max(threshold * 2, 0.01))
  const color = p >= threshold ? "bg-destructive" : p >= threshold / 2 ? "bg-amber-400" : "bg-lime"
  return (
    <div className="flex items-center gap-2 min-w-32">
      <div className="h-1.5 w-20 bg-secondary overflow-hidden"><div className={cn("h-full", color)} style={{ width: `${width}%` }} /></div>
      <span className="font-mono text-xs tabular-nums">{fmtPct(p)}</span>
    </div>
  )
}

export function SeverityBadge({ severity }: { severity: string }) {
  const styles: Record<string, string> = {
    CRITICAL: "bg-destructive/15 text-destructive border-destructive/30",
    HIGH: "bg-amber-400/10 text-amber-400 border-amber-400/30",
    MEDIUM: "bg-sky-400/10 text-sky-400 border-sky-400/30",
    LOW: "bg-secondary text-muted-foreground border-border",
  }
  return <span className={cn("text-[10px] font-mono px-1.5 py-0.5 border uppercase tracking-wider", styles[severity] ?? styles.LOW)}>{severity}</span>
}

export function ComponentTag({ component }: { component: string }) {
  const c: Record<string, string> = { BRAKE: "text-sky-400", POWERTRAIN: "text-amber-400", BATTERY: "text-lime" }
  return <span className={cn("text-xs font-mono uppercase tracking-wider", c[component] ?? "text-muted-foreground")}>{component}</span>
}

export function ErrorLine({ error }: { error: string | null }) {
  return error ? <p className="text-xs text-destructive font-mono mb-3">{error}</p> : null
}
