"use client";

import { cn } from "@/lib/utils";

interface QualityGaugeProps {
  /** Valeur 0-100 */
  value: number;
  size?: number;
  strokeWidth?: number;
  label?: string;
  /** Seuils personnalisés (par défaut : >=80 bon, >=50 attention, sinon critique) */
  thresholds?: { good: number; warn: number };
  className?: string;
}

/**
 * Jauge circulaire — élément visuel signature de l'application, réutilisé partout
 * où une métrique de qualité/complétude apparaît (Vue d'ensemble, Valeurs
 * manquantes, Qualité ML). Un seul motif visuel fort, décliné avec discipline,
 * plutôt qu'une profusion de styles de carte différents par page.
 */
export function QualityGauge({
  value,
  size = 96,
  strokeWidth = 8,
  label,
  thresholds = { good: 80, warn: 50 },
  className,
}: QualityGaugeProps) {
  const clamped = Math.max(0, Math.min(100, value));
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - clamped / 100);

  const colorClass =
    clamped >= thresholds.good ? "stroke-success" : clamped >= thresholds.warn ? "stroke-warning" : "stroke-destructive";
  const textColorClass =
    clamped >= thresholds.good ? "text-success" : clamped >= thresholds.warn ? "text-warning" : "text-destructive";

  return (
    <div className={cn("relative inline-flex flex-col items-center justify-center", className)} style={{ width: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={radius} strokeWidth={strokeWidth} className="fill-none stroke-muted" />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          className={cn("fill-none transition-all duration-700 ease-out", colorClass)}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className={cn("font-mono text-lg font-semibold tabular-nums", textColorClass)}>{Math.round(clamped)}</span>
        {label && <span className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</span>}
      </div>
    </div>
  );
}
