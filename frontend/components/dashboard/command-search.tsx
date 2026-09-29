"use client"

import { useEffect, useState, useCallback } from "react"
import { useRouter } from "next/navigation"
import { Activity, AlertTriangle, Bot, Car, Cpu, ListOrdered, Search, Workflow } from "lucide-react"
import { CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandSeparator } from "@/components/ui/command"
import { api, type Page, type VehicleRow } from "@/lib/api"

const pages = [
  { name: "Pulse overview", href: "/", icon: Activity },
  { name: "Maintenance priority queue", href: "/priority", icon: ListOrdered },
  { name: "Live alerts", href: "/alerts", icon: AlertTriangle },
  { name: "Vehicles", href: "/vehicles", icon: Car },
  { name: "Fleet assistant (AI)", href: "/assistant", icon: Bot },
  { name: "Model performance", href: "/models", icon: Cpu },
  { name: "Pipeline health", href: "/pipeline", icon: Workflow },
]

export function useCommandSearch() {
  const [open, setOpen] = useState(false)
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); setOpen((o) => !o) }
    }
    document.addEventListener("keydown", down)
    return () => document.removeEventListener("keydown", down)
  }, [])
  return { open, setOpen }
}

export function CommandSearch({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const router = useRouter()
  const [q, setQ] = useState("")
  const [matches, setMatches] = useState<VehicleRow[]>([])

  useEffect(() => {
    const vinPrefix = q.trim().toUpperCase()
    if (vinPrefix.length < 4 || !/^[A-HJ-NPR-Z0-9]+$/.test(vinPrefix)) { setMatches([]); return }
    const id = setTimeout(() => {
      api<Page<VehicleRow>>(`/vehicles?limit=8&q=${encodeURIComponent(vinPrefix)}`).then((p) => setMatches(p.items)).catch(() => setMatches([]))
    }, 200)
    return () => clearTimeout(id)
  }, [q])

  const go = useCallback((href: string) => { onOpenChange(false); setQ(""); router.push(href) }, [router, onOpenChange])

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange}>
      <CommandInput placeholder="Type a VIN prefix (e.g. 5YJC) or a page..." value={q} onValueChange={setQ} />
      <CommandList>
        <CommandEmpty>No results.</CommandEmpty>
        {matches.length > 0 && (
          <CommandGroup heading="Vehicles">
            {matches.map((v) => (
              <CommandItem key={v.vehicle_id} value={`${v.vin} ${v.make} ${v.model}`} onSelect={() => go(`/vehicles/${v.vehicle_id}`)}>
                <Search className="mr-2 h-4 w-4" />
                <span className="font-mono">{v.vin}</span>
                <span className="ml-2 text-muted-foreground text-xs">{v.vehicle_type} {v.make} {v.model}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        )}
        <CommandSeparator />
        <CommandGroup heading="Pages">
          {pages.map((p) => (
            <CommandItem key={p.href} value={p.name} onSelect={() => go(p.href)}>
              <p.icon className="mr-2 h-4 w-4" />{p.name}
            </CommandItem>
          ))}
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  )
}
