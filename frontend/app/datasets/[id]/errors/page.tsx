"use client";

import { PageHeader } from "@/components/layout/PageHeader";
import { ErrorState, LoadingState } from "@/components/shared/States";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { CheckCircle2 } from "lucide-react";
import { useDataset } from "@/lib/DatasetContext";
import { useApiResource } from "@/lib/useApiResource";
import { getErrorLog } from "@/lib/api";

export default function ErrorsPage() {
  const { datasetId } = useDataset();
  const { data, loading, error } = useApiResource(() => getErrorLog(datasetId), [datasetId]);

  return (
    <div>
      <PageHeader title="Journal des erreurs" description="Lignes ignorées lors du chargement initial du CSV (erreurs de parsing)." />

      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}

      {data && (
        <>
          {data.error_count === 0 ? (
            <Alert variant="success">
              <CheckCircle2 />
              <AlertTitle>Aucune erreur</AlertTitle>
              <AlertDescription>Toutes les lignes du fichier ont été chargées avec succès.</AlertDescription>
            </Alert>
          ) : (
            <Card>
              <CardHeader>
                <CardTitle>{data.error_count} ligne(s) ignorée(s)</CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="text-right">Ligne</TableHead>
                      <TableHead>Type</TableHead>
                      <TableHead>Message</TableHead>
                      <TableHead>Contenu brut</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.erreurs.map((e, i) => (
                      <TableRow key={i}>
                        <TableCell className="text-right tabular-nums">{e.ligne}</TableCell>
                        <TableCell className="font-mono text-xs">{e.erreur_type}</TableCell>
                        <TableCell className="text-muted-foreground">{e.erreur_msg}</TableCell>
                        <TableCell className="max-w-xs truncate font-mono text-xs text-muted-foreground">{e.raw_line}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
