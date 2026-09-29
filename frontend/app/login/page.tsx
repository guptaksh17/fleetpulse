"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { login } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

const DEMO_USERS = [
  { email: "manager@fleetpulse.local", label: "Fleet manager (tenant A)" },
  { email: "viewer@fleetpulse.local", label: "Viewer (tenant B)" },
  { email: "admin@fleetpulse.local", label: "Platform admin" },
]

export default function LoginPage() {
  const router = useRouter()
  const [email, setEmail] = useState(DEMO_USERS[0].email)
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true); setError(null)
    try { await login(email, password); router.replace("/") } catch (err) { setError(err instanceof Error ? err.message : "Login failed") } finally { setBusy(false) }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4 noise-overlay">
      <form onSubmit={submit} className="w-full max-w-sm bg-card border border-border p-8">
        <div className="flex items-center gap-3 mb-8">
          <div className="w-10 h-10 bg-lime flex items-center justify-center lime-glow-sm"><span className="text-background font-mono font-bold">F</span></div>
          <div>
            <h1 className="text-xl font-semibold tracking-tight">FleetPulse</h1>
            <p className="text-xs font-mono text-muted-foreground uppercase tracking-wider">Predictive maintenance</p>
          </div>
        </div>
        <label className="text-xs text-muted-foreground font-mono uppercase tracking-wider">Account</label>
        <select value={email} onChange={(e) => setEmail(e.target.value)} className="w-full mt-1 mb-4 bg-surface border border-border px-3 py-2 text-sm">
          {DEMO_USERS.map((u) => <option key={u.email} value={u.email}>{u.label}: {u.email}</option>)}
        </select>
        <label className="text-xs text-muted-foreground font-mono uppercase tracking-wider">Password</label>
        <Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} className="mt-1 mb-4" />
        {error && <p className="text-xs text-destructive font-mono mb-3">{error}</p>}
        <Button type="submit" disabled={busy || !password} className="w-full bg-lime text-background hover:bg-lime/90">{busy ? "Signing in..." : "Sign in"}</Button>
        <p className="text-[11px] text-muted-foreground mt-4">JWT sessions, role-based access and tenant isolation. Every data request is audited.</p>
      </form>
    </div>
  )
}
