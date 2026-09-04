/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // 'standalone' : Next.js trace les dépendances réellement utilisées et
  // produit un serveur autonome minimal (server.js) — évite d'embarquer tout
  // node_modules dans l'image Docker finale (DQE-12, voir frontend/Dockerfile).
  output: "standalone",
  // En dev, le backend FastAPI tourne sur :8000. En prod, NEXT_PUBLIC_API_URL
  // doit pointer vers l'URL réelle du backend (cf. docker-compose, DQE-12).
};
module.exports = nextConfig;
