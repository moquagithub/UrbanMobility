"use client";

import { PageHeader } from "@/components/layout/PageHeader";
import { StatCard } from "@/components/shared/StatCard";
import { StatCardsSkeleton, ErrorState } from "@/components/shared/States";
import { QualityGauge } from "@/components/shared/QualityGauge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useDataset } from "@/lib/DatasetContext";

export default function OverviewPage() {
  const { overview, loading, error } = useDataset();

  return (
    <div>
      <PageHeader title="Vue d'ensemble" description="Portrait général du dataset — volumétrie, complétude et profil par variable." />

      {loading && <StatCardsSkeleton count={8} />}
      {error && <ErrorState message={error} />}

      {overview && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <StatCard label="Lignes" value={overview.nb_lignes.toLocaleString("fr-FR")} />
            <StatCard label="Colonnes" value={overview.nb_colonnes.toLocaleString("fr-FR")} />
            <StatCard label="Doublons" value={overview.doublons.toLocaleString("fr-FR")} />
            <StatCard label="Colonnes PII traitées" value={overview.pii_colonnes_traitees} />
            <StatCard label="Colonnes numériques" value={overview.colonnes_numeriques} />
            <StatCard label="Colonnes textuelles" value={overview.colonnes_textuelles} />
            <StatCard label="Valeurs manquantes" value={overview.valeurs_manquantes_total.toLocaleString("fr-FR")} />
            <StatCard label="Analysé le" value={overview.date_analyse} />
          </div>

          <Card>
            <CardHeader className="flex-row items-center justify-between space-y-0">
              <div>
                <CardTitle>Complétude globale</CardTitle>
              </div>
            </CardHeader>
            <CardContent className="flex items-center gap-6">
              <QualityGauge value={overview.taux_completude_global} label="complétude" size={88} />
              <p className="text-sm text-muted-foreground">
                {overview.taux_completude_global.toFixed(1)}% des cellules du dataset sont renseignées.{" "}
                {overview.taux_completude_global < 80 && "Consultez la page Valeurs manquantes pour le détail par variable."}
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Profil par variable</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Variable</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead className="text-right">Complétude</TableHead>
                    <TableHead className="text-right">Manquants</TableHead>
                    <TableHead className="text-right">Uniques</TableHead>
                    <TableHead>Mode</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {overview.colonnes.slice(0, 50).map((col) => (
                    <TableRow key={col.nom_anonyme}>
                      <TableCell className="font-mono text-xs">{col.nom_anonyme}</TableCell>
                      <TableCell className="text-muted-foreground">{col.dtype}</TableCell>
                      <TableCell className="text-right tabular-nums">{col.taux_completude.toFixed(1)}%</TableCell>
                      <TableCell className="text-right tabular-nums">{col.valeurs_manquantes}</TableCell>
                      <TableCell className="text-right tabular-nums">{col.valeurs_uniques}</TableCell>
                      <TableCell className="max-w-[200px] truncate text-muted-foreground">
                        {col.valeur_la_plus_freq ?? "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              {overview.colonnes.length > 50 && (
                <p className="px-4 py-3 text-xs text-muted-foreground">
                  Affichage limité aux 50 premières variables sur {overview.colonnes.length}.
                </p>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
