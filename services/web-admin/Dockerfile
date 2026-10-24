# Сборка SPA и раздача статики nginx. В проде перед ним стоит Caddy (TLS + /api,
# /admin → core-api) — см. Phase 5. Сам контейнер отдаёт только статику с
# SPA-fallback на index.html.
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci || npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
