import { defineConfig, devices } from "@playwright/test";

/**
 * Configuration Playwright (DQE-13) — tests d'intégration bout-en-bout du
 * parcours critique (inscription → upload → consultation → déconnexion).
 *
 * IMPORTANT — limite de vérification : ces tests ont été écrits mais n'ont
 * PAS pu être exécutés dans l'environnement de développement de cette tâche
 * (pas d'accès réseau au CDN de binaires navigateur de Playwright, voir
 * README §12.6). Ils sont prêts à tourner en CI (`.github/workflows/ci.yml`,
 * job `e2e`, qui a un accès réseau complet) mais leur première exécution
 * réelle doit être surveillée attentivement — traitez-les comme du code
 * relu mais non testé, pas comme une suite déjà validée.
 *
 * Prérequis pour lancer localement : backend sur :8000 ET frontend sur
 * :3000 déjà démarrés (voir README §1) — ce fichier ne les lance pas lui-même.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false, // les tests partagent un compte/dataset créés à la volée — éviter les collisions
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
