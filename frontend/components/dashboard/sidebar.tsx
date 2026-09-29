"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { cn } from "@/lib/utils"
import { Activity, AlertTriangle, Bot, Car, Command, Cpu, ListOrdered, LogOut, Search, Workflow, X } from "lucide-react"
import { useAuth } from "@/context/auth-context"

const navItems = [
  { href: "/", label: "Pulse", icon: Activity, description: "Overview" },
  { href: "/priority", label: "Priority", icon: ListOrdered, description: "What to fix" },
  { href: "/alerts", label: "Alerts", icon: AlertTriangle, description: "Live" },
  { href: "/vehicles", label: "Vehicles", icon: Car, description: "Fleet" },
  { href: "/assistant", label: "Assistant", icon: Bot, description: "Ask AI" },
  { href: "/models", label: "Models", icon: Cpu, description: "Accuracy" },
  { href: "/pipeline", label: "Pipeline", icon: Workflow, description: "Health" },
]

interface SidebarProps { onOpenCommand?: () => void; isOpen?: boolean; onClose?: () => void }

export function Sidebar({ onOpenCommand, isOpen, onClose }: SidebarProps) {
  const pathname = usePathname()
  const { me, logout } = useAuth()
  const initials = me?.email ? me.email.slice(0, 2).toUpperCase() : "--"

  return (
    <>
      {isOpen && <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40 lg:hidden" onClick={onClose} />}
      <aside className={cn(
        "fixed left-0 top-0 h-screen w-64 flex flex-col border-r border-border bg-background z-50 transition-transform duration-300",
        "lg:translate-x-0", isOpen ? "translate-x-0" : "-translate-x-full",
      )}>
        <div className="p-6 border-b border-border">
          <div className="flex items-center justify-between">
            <Link href="/" className="flex items-center gap-3" onClick={onClose}>
              <div className="w-8 h-8 bg-lime flex items-center justify-center">
                <span className="text-background font-mono text-sm font-bold">F</span>
              </div>
              <div>
                <span className="text-lg font-semibold tracking-tight block leading-tight">FleetPulse</span>
                <span className="text-[10px] font-mono text-muted-foreground uppercase tracking-wider">Predictive maintenance</span>
              </div>
            </Link>
            <button onClick={onClose} className="lg:hidden p-1 hover:bg-surface-hover transition-colors" aria-label="Close menu"><X className="w-5 h-5" /></button>
          </div>
        </div>

        <div className="p-4">
          <button onClick={onOpenCommand} className="w-full flex items-center gap-3 px-3 py-2 text-sm text-muted-foreground bg-surface border border-border hover:bg-surface-hover transition-colors">
            <Search className="w-4 h-4" />
            <span className="flex-1 text-left">Search VIN or page...</span>
            <kbd className="flex items-center gap-1 text-xs font-mono bg-background px-1.5 py-0.5 border border-border"><Command className="w-3 h-3" />K</kbd>
          </button>
        </div>

        <nav className="flex-1 p-4 space-y-1">
          {navItems.map((item) => {
            const isActive = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href)
            return (
              <Link key={item.href} href={item.href} onClick={onClose}
                className={cn("flex items-center gap-3 px-3 py-2.5 text-sm transition-all group relative",
                  isActive ? "text-accent-foreground bg-accent" : "text-muted-foreground hover:text-foreground hover:bg-surface-hover")}>
                {isActive && <div className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-4 bg-lime lime-glow-sm" />}
                <item.icon className="w-4 h-4" />
                <span className="font-medium">{item.label}</span>
                <span className="ml-auto text-xs text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity">{item.description}</span>
              </Link>
            )
          })}
        </nav>

        <div className="p-4 border-t border-border">
          <div className="flex items-center gap-3 px-3">
            <div className="w-8 h-8 bg-surface border border-border flex items-center justify-center">
              <span className="text-xs font-mono text-muted-foreground">{initials}</span>
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium truncate">{me?.email ?? "..."}</p>
              <p className="text-xs text-muted-foreground truncate font-mono">{me?.role ?? ""}</p>
            </div>
            <button onClick={logout} className="p-1.5 hover:bg-surface-hover transition-colors text-muted-foreground" title="Sign out" aria-label="Sign out">
              <LogOut className="w-4 h-4" />
            </button>
          </div>
        </div>
      </aside>
    </>
  )
}
