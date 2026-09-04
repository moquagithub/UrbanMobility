import { LineChart } from "lucide-react";
import UploadForm from "@/components/UploadForm";
import { MyDatasetsList } from "@/components/datasets/MyDatasetsList";

export default function HomePage() {
  return (
    <main className="flex min-h-screen flex-col items-center gap-8 bg-muted/30 px-4 py-16">
      <div className="flex flex-col items-center gap-3 text-center">
        <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-primary text-primary-foreground">
          <LineChart className="h-5 w-5" />
        </div>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">data-quiz-eda</h1>
        <p className="max-w-md text-sm text-muted-foreground">
          Déposez un export CSV pour lancer l&apos;analyse exploratoire : complétude, distributions,
          corrélations, importance des variables et recommandations de préparation des données.
        </p>
      </div>

      <UploadForm />
      <MyDatasetsList />
    </main>
  );
}
