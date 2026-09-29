# FleetPulse web dashboard (Next.js standalone build).
# API_URL is baked into the /api/v1 rewrite at build time.
FROM node:22-alpine AS build
WORKDIR /app
RUN npm install -g pnpm@10.10.0
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile --config.virtual-store-dir=node_modules/.pnpm
COPY . .
ARG API_URL=http://api:8000
ENV API_URL=$API_URL NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0 NEXT_TELEMETRY_DISABLED=1
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
