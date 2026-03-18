# ── Stage 1: Build ───────────────────────────────────────────────────────────
FROM node:20-alpine AS build

WORKDIR /app

# Install pnpm
RUN corepack enable && corepack prepare pnpm@latest --activate

# Install dependencies (cached layer)
COPY web/package.json web/pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile 2>/dev/null || pnpm install

# Build
COPY web/ ./
RUN pnpm build

# ── Stage 2: Nginx ──────────────────────────────────────────────────────────
FROM nginx:alpine

# Remove default config
RUN rm /etc/nginx/conf.d/default.conf

# Copy nginx config template
COPY web/nginx.conf /etc/nginx/templates/default.conf.template

# Copy built assets
COPY --from=build /app/dist /usr/share/nginx/html

# Default backend URL (override via docker-compose environment)
ENV BACKEND_URL=http://localhost:8765

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
