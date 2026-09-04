import { test, expect } from "@playwright/test";
import path from "path";

/**
 * Parcours critique bout-en-bout : upload → vue d'ensemble → navigation EDA.
 * Voir playwright.config.ts pour la limite de vérification importante (non
 * exécuté par Claude dans l'environnement de développement — traiter comme
 * du code relu, pas comme une suite déjà validée).
 *
 * Nécessite le backend ET le frontend démarrés (voir README §1), pointant
 * l'un vers l'autre (NEXT_PUBLIC_API_URL cohérent). L'application est en
 * accès libre : aucune inscription ni connexion n'est nécessaire.
 */

const SAMPLE_CSV = path.join(__dirname, "fixtures", "sample.csv");

test.describe("Parcours critique", () => {
  test("upload → vue d'ensemble → navigation dans la barre latérale", async ({ page }) => {
    await page.goto("/");

    // ── Upload ────────────────────────────────────────────────────────
    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles(SAMPLE_CSV);

    // Redirection vers la Vue d'ensemble du dataset créé.
    await expect(page).toHaveURL(/\/datasets\/[a-f0-9]+\/overview/, { timeout: 15_000 });
    await expect(page.getByRole("heading", { name: "Vue d'ensemble" })).toBeVisible();
    await expect(page.getByText("Lignes")).toBeVisible();
    await expect(page.getByText("Colonnes")).toBeVisible();

    // ── Navigation vers une autre page EDA via la barre latérale ────────
    await page.getByRole("link", { name: "Valeurs manquantes" }).click();
    await expect(page.getByRole("heading", { name: "Valeurs manquantes" })).toBeVisible();
  });

  test("un dataset_id inconnu affiche la page 'Dataset introuvable'", async ({ page }) => {
    await page.goto("/datasets/0000000000000000000000000000dead/overview");
    await expect(page.getByText("Dataset introuvable")).toBeVisible({ timeout: 15_000 });
  });

  test("le dataset uploadé est accessible depuis n'importe quel navigateur", async ({ browser }) => {
    const contextA = await browser.newContext();
    const contextB = await browser.newContext();
    const pageA = await contextA.newPage();
    const pageB = await contextB.newPage();

    await pageA.goto("/");
    await pageA.locator('input[type="file"]').setInputFiles(SAMPLE_CSV);
    await expect(pageA).toHaveURL(/\/datasets\/[a-f0-9]+\/overview/, { timeout: 15_000 });
    const datasetUrl = pageA.url();

    // Aucun cloisonnement : la même URL est consultable depuis un autre contexte.
    await pageB.goto(datasetUrl);
    await expect(pageB.getByRole("heading", { name: "Vue d'ensemble" })).toBeVisible({ timeout: 15_000 });

    await contextA.close();
    await contextB.close();
  });
});
