# syntax=docker/dockerfile:1
# Pinned base image (version + SHA256 digest) per security policy.
FROM node:20.18.1-alpine3.20@sha256:9b5af1cc895fe7231c351cc1ea653322742091fb5d1ea8f1eb404c11e2b4da56 AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:20.18.1-alpine3.20@sha256:9b5af1cc895fe7231c351cc1ea653322742091fb5d1ea8f1eb404c11e2b4da56 AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Baked into the client at build time (a second stack sets its own backend port).
ARG NEXT_PUBLIC_API_BASE=http://localhost:8000
ARG NEXT_PUBLIC_WS_BASE=ws://localhost:8000
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE NEXT_PUBLIC_WS_BASE=$NEXT_PUBLIC_WS_BASE
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

RUN mkdir -p /app/public

FROM node:20.18.1-alpine3.20@sha256:9b5af1cc895fe7231c351cc1ea653322742091fb5d1ea8f1eb404c11e2b4da56 AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
RUN addgroup -S appgroup && adduser -S appuser -G appgroup
# Next.js standalone output (see next.config.js output: "standalone").
COPY --from=builder --chown=appuser:appgroup /app/.next/standalone ./
COPY --from=builder --chown=appuser:appgroup /app/.next/static ./.next/static
RUN mkdir -p /app/public
COPY --from=builder --chown=appuser:appgroup /app/public ./public
USER appuser
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD wget --no-verbose --tries=1 --spider http://$(hostname):3000 || exit 1
CMD ["node", "server.js"]
