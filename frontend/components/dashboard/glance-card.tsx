"use client"

import React from "react"

import { useEffect, useState, useRef } from "react"
import { motion, useMotionValue, useSpring, useTransform } from "framer-motion"
import { cn } from "@/lib/utils"
import { ArrowUp, ArrowDown } from "lucide-react"
import { Area, AreaChart, ResponsiveContainer } from "recharts"

interface GlanceCardProps {
  title: string
  value: number
  prefix?: string
  suffix?: string
  change?: number
  sparklineData?: number[]
  caption?: string
  format?: (n: number) => string
  className?: string
}

function formatCompactNumber(num: number): string {
  if (num >= 1000000) {
    return (num / 1000000).toFixed(num >= 10000000 ? 1 : 2).replace(/\.?0+$/, '') + 'M'
  }
  if (num >= 1000) {
    return (num / 1000).toFixed(num >= 10000 ? 1 : 2).replace(/\.?0+$/, '') + 'K'
  }
  return num.toLocaleString()
}

function useCountUp(end: number, duration: number = 2000) {
  const [count, setCount] = useState(0)
  const countRef = useRef(0)
  const startTimeRef = useRef<number | null>(null)

  useEffect(() => {
    const animate = (currentTime: number) => {
      if (startTimeRef.current === null) {
        startTimeRef.current = currentTime
      }

      const elapsed = currentTime - startTimeRef.current
      const progress = Math.min(elapsed / duration, 1)
      const easeOutQuart = 1 - Math.pow(1 - progress, 4)
      
      countRef.current = Math.floor(end * easeOutQuart)
      setCount(countRef.current)

      if (progress < 1) {
        requestAnimationFrame(animate)
      }
    }

    requestAnimationFrame(animate)
  }, [end, duration])

  return count
}

export function GlanceCard({
  title,
  value,
  prefix = "",
  suffix = "",
  change,
  sparklineData,
  caption,
  format = formatCompactNumber,
  className,
}: GlanceCardProps) {
  const cardRef = useRef<HTMLDivElement>(null)
  const animatedValue = useCountUp(value)

  const x = useMotionValue(0)
  const y = useMotionValue(0)

  const mouseXSpring = useSpring(x)
  const mouseYSpring = useSpring(y)

  const rotateX = useTransform(mouseYSpring, [-0.5, 0.5], ["5deg", "-5deg"])
  const rotateY = useTransform(mouseXSpring, [-0.5, 0.5], ["-5deg", "5deg"])

  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!cardRef.current) return

    const rect = cardRef.current.getBoundingClientRect()
    const width = rect.width
    const height = rect.height
    const mouseX = e.clientX - rect.left
    const mouseY = e.clientY - rect.top

    x.set((mouseX / width) - 0.5)
    y.set((mouseY / height) - 0.5)
  }

  const handleMouseLeave = () => {
    x.set(0)
    y.set(0)
  }

  const chartData = (sparklineData || []).map((value, index) => ({ value, index }))
  const isPositive = (change ?? 0) >= 0

  return (
    <motion.div
      ref={cardRef}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      style={{
        rotateX,
        rotateY,
        transformStyle: "preserve-3d",
      }}
      className={cn(
        "relative p-4 md:p-6 bg-card border border-border overflow-hidden group cursor-default",
        className
      )}
    >
      {/* Subtle gradient overlay on hover */}
      <div className="absolute inset-0 bg-gradient-to-br from-lime/5 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
      
      <div className="relative z-10">
        <p className="text-sm text-muted-foreground mb-2">{title}</p>
        
        <div className="flex items-baseline gap-1 md:gap-2 mb-3 md:mb-4">
          <span className="text-lg sm:text-2xl md:text-4xl font-mono font-semibold tracking-tighter">
            {prefix}{format(animatedValue)}{suffix}
          </span>
          {change !== undefined && <div
            className={cn(
              "flex items-center gap-0.5 text-sm font-medium",
              isPositive ? "text-lime" : "text-destructive"
            )}
          >
            {isPositive ? (
              <ArrowUp className="w-3 h-3" />
            ) : (
              <ArrowDown className="w-3 h-3" />
            )}
            <span>{Math.abs(change)}%</span>
          </div>}
        </div>
        {caption && <p className="text-xs font-mono text-muted-foreground uppercase tracking-wider">{caption}</p>}

        {chartData.length > 0 && <div className="h-12 -mx-2 mt-2">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={chartData}>
              <defs>
                <linearGradient id={`gradient-${title.replace(/\s+/g, '-')}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--lime)" stopOpacity={0.4} />
                  <stop offset="100%" stopColor="var(--card)" stopOpacity={0} />
                </linearGradient>
              </defs>
              <Area
                type="monotone"
                dataKey="value"
                stroke="var(--lime)"
                strokeWidth={2}
                fill={`url(#gradient-${title.replace(/\s+/g, '-')})`}
                dot={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>}
      </div>
    </motion.div>
  )
}
