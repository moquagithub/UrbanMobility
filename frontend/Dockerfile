# frontend/Dockerfile
# =====================
# Image de production pour le frontend Next.js (DQE-12) — build multi-stage :
# 1) deps    : installe node_modules seul (couche mise en cache si package*.json ne change pas)
# 2) builder : build de production (trace les dépendances réellement utilisées)
# 3) runner  : image finale minimale — sortie 'standalone' de Next.js (voir next.config.js),
#              pas tout node_modules embarqué.

FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# NEXT_PUBLIC_* est inliné dans le bundle JS au moment du BUILD, pas lu au
# runtime — d'où cet ARG plutôt qu'une simple variable d'environnement du
# conteneur final. Par défaut vide : en chaîne vide, lib/api.ts construit des
# URLs relatives (`/api/v1/...`), qui fonctionnent nativement derrière le
# reverse-proxy nginx fourni (voir docker-compose.yml + nginx/nginx.conf) —
# aucune configuration CORS cross-origin nécessaire dans ce mode de déploiement.
# Surchargez au build (`--build-arg NEXT_PUBLIC_API_URL=https://api.example.com`)
# uniquement si le backend est exposé sous un domaine distinct sans proxy unifié.
ARG NEXT_PUBLIC_API_URL=""
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
ENV NEXT_TELEMETRY_DISABLED=1

RUN npm run build

FROM node:22-slim AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000

# Utilisateur non-root — bonne pratique standard pour une image de prod.
RUN addgroup --system --gid 1001 nodejs && adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD node -e "require('http').get('http://localhost:3000/', r => process.exit(r.statusCode < 500 ? 0 : 1)).on('error', () => process.exit(1))"

CMD ["node", "server.js"]
