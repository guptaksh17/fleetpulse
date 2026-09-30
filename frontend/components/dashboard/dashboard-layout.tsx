"use client"

import React, { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { motion } from "framer-motion"
import { Menu, Search } from "lucide-react"
import { toast } from "sonner"
import { Sidebar } from "./sidebar"
import { CommandSearch, useCommandSearch } from "./command-search"
import { AuthProvider, useAuth } from "@/context/auth-context"
import { api, fmtPct, getToken, type Alert, type Page } from "@/lib/api"

/** Pops a toast for new urgent alerts: every RULE (DTC fault) alert and CRITICAL ML alerts. Other ML alerts are only listed on the Alerts page. */
function LiveAlertToaster() {
  const seen = useRef<Set<string> | null>(null)
  const { me } = useAuth()
  const canAck = useRef(false)
  canAck.current = me?.role === "ADMIN" || me?.role === "FLEET_MANAGER"
  useEffect(() => {
    const tick = async () => {
      if (!getToken()) return
      try {
        const page = await api<Page<Alert>>("/alerts?limit=20")
        if (seen.current === null) { seen.current = new Set(page.items.map((a) => a.alert_id)); return }
        for (const a of page.items.slice().reverse()) {
          if (seen.current.has(a.alert_id)) continue
          seen.current.add(a.alert_id)
          if (a.source !== "RULE" && a.severity !== "CRITICAL") continue
          const title = a.source === "RULE" ? `${a.alert_type} on ${a.vin}` : `${a.component} risk ${fmtPct(a.risk_probability)} on ${a.vin}`
          const fn = a.severity === "CRITICAL" ? toast.error : toast.warning
          const open = { label: "Open", onClick: () => (window.location.href = `/vehicles/${a.vehicle_id}`) }
          const ack = {
            label: "Acknowledge",
            onClick: () => api(`/alerts/${a.alert_id}/acknowledge`, { method: "POST" })
              .then(() => toast.success("Alert acknowledged (audited)"))
              .catch((e) => toast.error(e instanceof Error ? e.message : "Failed")),
          }
          fn(title, { description: a.message, duration: 20000, ...(canAck.current ? { action: ack, cancel: open } : { action: open }) })
        }
      } catch { /* the page shows its own errors */ }
    }
    tick()
    const id = setInterval(tick, 2000)
    return () => clearInterval(id)
  }, [])
  return null
}

export function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { open, setOpen } = useCommandSearch()
  const [sidebarOpen, setSidebarOpen] = useState(false)

  return (
    <AuthProvider>
      <div className="min-h-screen bg-background noise-overlay">
        <header className="fixed top-0 left-0 right-0 h-14 bg-background/80 backdrop-blur-xl border-b border-border z-30 lg:hidden">
          <div className="flex items-center justify-between h-full px-4">
            <div className="flex items-center gap-3">
              <button onClick={() => setSidebarOpen(true)} className="p-2 hover:bg-surface-hover transition-colors" aria-label="Open menu">
                <Menu className="w-5 h-5" />
              </button>
              <Link href="/" className="flex items-center gap-2">
                <div className="w-6 h-6 bg-lime flex items-center justify-center">
                  <span className="text-background font-mono text-xs font-bold">F</span>
                </div>
                <span className="font-semibold">FleetPulse</span>
              </Link>
            </div>
            <button onClick={() => setOpen(true)} className="p-2 hover:bg-surface-hover transition-colors" aria-label="Search">
              <Search className="w-5 h-5" />
            </button>
          </div>
        </header>

        <Sidebar onOpenCommand={() => setOpen(true)} isOpen={sidebarOpen} onClose={() => setSidebarOpen(false)} />
        <CommandSearch open={open} onOpenChange={setOpen} />

        <main className="lg:pl-64 pt-14 lg:pt-0">
          <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2, ease: "easeOut" }}>
            {children}
          </motion.div>
        </main>
        <LiveAlertToaster />
      </div>
    </AuthProvider>
  )
}
