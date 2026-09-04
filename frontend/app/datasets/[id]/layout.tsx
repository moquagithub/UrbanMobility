import { Sidebar } from "@/components/layout/Sidebar";
import { DatasetGuard } from "@/components/layout/DatasetGuard";
import { DatasetProvider } from "@/lib/DatasetContext";

export default function DatasetLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: { id: string };
}) {
  return (
    <DatasetProvider datasetId={params.id}>
      <div className="flex">
        <Sidebar datasetId={params.id} />
        <main className="min-h-screen flex-1 overflow-x-hidden bg-muted/30 px-6 py-6 sm:px-8 sm:py-8">
          <div className="mx-auto max-w-6xl">
            <DatasetGuard>{children}</DatasetGuard>
          </div>
        </main>
      </div>
    </DatasetProvider>
  );
}
