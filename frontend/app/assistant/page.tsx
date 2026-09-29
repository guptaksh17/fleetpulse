"use client"

import { Suspense, useEffect, useRef, useState } from "react"
import { useSearchParams } from "next/navigation"
import { Bot, Send, ShieldCheck, User, Wrench } from "lucide-react"
import { DashboardLayout } from "@/components/dashboard/dashboard-layout"
import { PageHeader } from "@/components/dashboard/fleet-ui"
import { Button } from "@/components/ui/button"
import { api, type AssistantReply } from "@/lib/api"

const SUGGESTIONS = [
  "What should I service first this week?",
  "Which batteries are most at risk and what will they cost me?",
  "Give me a summary of the fleet's health.",
  "Show me the latest critical alerts.",
]
type Msg = { role: "user" | "assistant"; text: string; meta?: AssistantReply }

/** Minimal markdown: bold and pipe tables, enough for the assistant's answers. */
function RichText({ text }: { text: string }) {
  const lines = text.split("\n")
  const out: React.ReactNode[] = []
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i]
    if (l.trim().startsWith("|") && lines[i + 1]?.includes("---")) {
      const header = l.split("|").slice(1, -1).map((c) => c.trim())
      const body: string[][] = []
      i += 2
      while (i < lines.length && lines[i].trim().startsWith("|")) { body.push(lines[i].split("|").slice(1, -1).map((c) => c.trim())); i++ }
      i--
      out.push(<table key={i} className="my-2 text-xs w-full"><thead><tr>{header.map((h, j) => <th key={j} className="text-left font-mono text-muted-foreground pr-3 pb-1">{h}</th>)}</tr></thead>
        <tbody>{body.map((r, k) => <tr key={k} className="border-t border-border">{r.map((c, j) => <td key={j} className="pr-3 py-1 font-mono">{c}</td>)}</tr>)}</tbody></table>)
      continue
    }
    const parts = l.split(/(\*\*[^*]+\*\*)/g).map((p, j) => (p.startsWith("**") ? <strong key={j}>{p.slice(2, -2)}</strong> : <span key={j}>{p}</span>))
    out.push(<p key={i} className="min-h-[1em]">{parts}</p>)
  }
  return <div className="text-sm leading-relaxed">{out}</div>
}

function AssistantInner() {
  const params = useSearchParams()
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [q, setQ] = useState("")
  const [busy, setBusy] = useState(false)
  const end = useRef<HTMLDivElement>(null)
  const asked = useRef(false)

  const ask = async (question: string) => {
    if (!question.trim() || busy) return
    setMsgs((m) => [...m, { role: "user", text: question }]); setQ(""); setBusy(true)
    try {
      const r = await api<AssistantReply>("/assistant", { method: "POST", body: JSON.stringify({ question }) })
      setMsgs((m) => [...m, { role: "assistant", text: r.answer, meta: r }])
    } catch (e) {
      setMsgs((m) => [...m, { role: "assistant", text: e instanceof Error ? e.message : "Request failed" }])
    } finally { setBusy(false) }
  }

  useEffect(() => { const pre = params.get("q"); if (pre && !asked.current) { asked.current = true; ask(pre) } }, [params])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }) }, [msgs, busy])

  return (
    <div className="p-4 md:p-6 lg:p-8 flex flex-col min-h-[calc(100vh-3.5rem)] lg:min-h-screen">
      <PageHeader eyebrow="Agentic AI" title="Fleet Assistant"
        subtitle="Ask in plain English. The model can only call four read-only tools over your own tenant's data; every question is audited." />
      <div className="flex flex-wrap gap-2 mb-4 text-[11px] font-mono text-muted-foreground">
        <span className="flex items-center gap-1 border border-border px-2 py-1"><ShieldCheck className="w-3 h-3 text-lime" /> tenant-scoped tools</span>
        <span className="flex items-center gap-1 border border-border px-2 py-1"><Wrench className="w-3 h-3 text-lime" /> fleet_summary · top_priority · active_alerts · vehicle_detail</span>
      </div>
      <div className="flex-1 bg-card border border-border p-4 md:p-6 overflow-y-auto space-y-5">
        {msgs.length === 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {SUGGESTIONS.map((s) => <button key={s} onClick={() => ask(s)} className="text-left text-sm p-3 border border-border hover:bg-surface-hover hover:border-lime/40 transition-colors">{s}</button>)}
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className="flex gap-3">
            <div className={`w-7 h-7 shrink-0 flex items-center justify-center ${m.role === "user" ? "bg-surface border border-border" : "bg-lime"}`}>
              {m.role === "user" ? <User className="w-4 h-4" /> : <Bot className="w-4 h-4 text-background" />}
            </div>
            <div className="min-w-0 flex-1">
              {m.role === "user" ? <p className="text-sm">{m.text}</p> : <RichText text={m.text} />}
              {m.meta && (
                <p className="text-[11px] font-mono text-muted-foreground mt-2">
                  {m.meta.mode} · tools: {m.meta.tools_used.map((t) => `${t.tool}(${Object.entries(t.args).map(([k, v]) => `${k}=${v}`).join(", ")})`).join(", ") || "none"} · {Math.round(m.meta.latency_ms)} ms · audited
                  {m.meta.llm_error && ` · LLM unavailable (${m.meta.llm_error}), answered by rules`}
                </p>
              )}
            </div>
          </div>
        ))}
        {busy && <p className="text-xs font-mono text-muted-foreground pulse-live">thinking and calling tools...</p>}
        <div ref={end} />
      </div>
      <form onSubmit={(e) => { e.preventDefault(); ask(q) }} className="mt-4 flex gap-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} maxLength={500} placeholder="Ask about risk, alerts, costs or a VIN..."
          className="flex-1 bg-card border border-border px-4 py-3 text-sm focus:outline-none focus:border-lime/50" />
        <Button type="submit" disabled={busy || !q.trim()} className="bg-lime text-background hover:bg-lime/90 h-auto px-5"><Send className="w-4 h-4" /></Button>
      </form>
    </div>
  )
}

export default function Page() {
  return <DashboardLayout><Suspense><AssistantInner /></Suspense></DashboardLayout>
}
