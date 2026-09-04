"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  AlertTriangle,
  BarChart3,
  Gauge,
  Network,
  Star,
  SlidersHorizontal,
  Wrench,
  FileWarning,
  Upload,
  Boxes,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}

function navItems(datasetId: string): NavItem[] {
  const base = `/datasets/${datasetId}`;
  return [
    { href: `${base}/overview`, label: "Vue d'ensemble", icon: LayoutDashboard },
    { href: `${base}/missing`, label: "Valeurs manquantes", icon: AlertTriangle },
    { href: `${base}/distributions`, label: "Distributions", icon: BarChart3 },
    { href: `${base}/normality`, label: "Normalité", icon: Gauge },
    { href: `${base}/correlation`, label: "Corrélations", icon: Network },
    { href: `${base}/importance`, label: "Importance des variables", icon: Star },
    { href: `${base}/explorer`, label: "Exploration par variable", icon: SlidersHorizontal },
    { href: `${base}/recommendations`, label: "Recommandations", icon: Wrench },
    { href: `${base}/errors`, label: "Journal des erreurs", icon: FileWarning },
    { href: `${base}/ml`, label: "Clustering (ML)", icon: Boxes },
  ];
}

export function Sidebar({ datasetId }: { datasetId: string }) {
  const pathname = usePathname();
  const items = navItems(datasetId);

  return (
    <aside className="flex h-screen w-64 shrink-0 flex-col border-r border-border bg-card">
      <div className="flex items-center gap-2 border-b border-border px-5 py-4">
        <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary font-mono text-sm font-bold text-primary-foreground">
          EQ
        </div>
        <div>
          <div className="text-sm font-semibold leading-none text-foreground">data-quiz-eda</div>
          <div className="text-[11px] text-muted-foreground">Analyse exploratoire</div>
        </div>
      </div>

      <nav className="flex-1 space-y-0.5 overflow-y-auto px-2.5 py-3">
        {items.map((item) => {
          const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                active ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-accent/60 hover:text-foreground"
              )}
            >
              <Icon className="h-4 w-4 shrink-0" />
              <span className="truncate">{item.label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="space-y-0.5 border-t border-border p-2.5">
        <Link
          href="/"
          className="flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground"
        >
          <Upload className="h-4 w-4 shrink-0" />
          Nouveau fichier
        </Link>
      </div>
    </aside>
  );
}
