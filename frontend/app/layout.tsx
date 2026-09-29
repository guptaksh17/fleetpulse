import React from "react"
import type { Metadata, Viewport } from "next"
import { Geist, Geist_Mono } from "next/font/google"
import { Toaster } from "@/components/ui/sonner"
import "./globals.css"

const _geist = Geist({ subsets: ["latin"] })
const _geistMono = Geist_Mono({ subsets: ["latin"] })

export const metadata: Metadata = {
  title: "FleetPulse | Predictive Maintenance",
  description: "Calibrated 7-day component failure risk, a cost-ranked maintenance queue and live alerts for connected fleets",
  icons: { icon: "/icon.svg" },
}

export const viewport: Viewport = { themeColor: "#050505" }

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body className="font-sans antialiased bg-background text-foreground min-h-screen">
        {children}
        <Toaster theme="dark" position="bottom-right" />
      </body>
    </html>
  )
}
