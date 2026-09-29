"use client"

import React, { createContext, useContext, useEffect, useState } from "react"
import { usePathname, useRouter } from "next/navigation"
import { api, getToken, setToken, type Me } from "@/lib/api"

interface AuthState { me: Me | null; logout: () => void }
const AuthContext = createContext<AuthState>({ me: null, logout: () => {} })

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const router = useRouter()
  const pathname = usePathname()

  useEffect(() => {
    if (!getToken()) { router.replace("/login"); return }
    api<Me>("/me").then(setMe).catch(() => router.replace("/login"))
  }, [router, pathname])

  const logout = () => { setToken(null); setMe(null); router.replace("/login") }
  return <AuthContext.Provider value={{ me, logout }}>{children}</AuthContext.Provider>
}

export const useAuth = () => useContext(AuthContext)
