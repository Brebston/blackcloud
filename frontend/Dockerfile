# syntax=docker/dockerfile:1.7
# Образи можна закріпити за digest: scripts/pin-images.sh записує їх у .env
ARG NODE_IMAGE=node:22-alpine
ARG NGINX_IMAGE=nginxinc/nginx-unprivileged:1.27-alpine
FROM ${NODE_IMAGE} AS build
WORKDIR /app
COPY package.json package-lock.json* ./
# --ignore-scripts: postinstall-скрипти залежностей не виконуються під час збірки.
# package-lock.json (make lock) фіксує версії та хеші integrity всіх пакетів.
RUN if [ -f package-lock.json ]; then npm ci --ignore-scripts; else npm install --ignore-scripts; fi
COPY . .
RUN npm run build

FROM ${NGINX_IMAGE}
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
