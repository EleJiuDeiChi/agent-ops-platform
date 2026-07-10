FROM node:22.23.1-bookworm-slim AS build

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/public ./public
COPY frontend/src ./src
RUN npm run build

FROM alpine:3.23.5 AS runtime

ARG NGINX_SITE_CONFIG=nginx.prod.conf
RUN apk upgrade --no-cache \
    && apk add --no-cache 'nginx~1.28.3'
COPY deploy/nginx.conf /etc/nginx/nginx.conf
COPY deploy/${NGINX_SITE_CONFIG} /etc/nginx/conf.d/default.conf
COPY --from=build --chown=nginx:nginx /build/dist /usr/share/nginx/html

RUN mkdir -p /tmp/client_temp /tmp/proxy_temp /tmp/fastcgi_temp \
        /tmp/uwsgi_temp /tmp/scgi_temp /var/lib/nginx/logs \
    && ln -sf /dev/stderr /var/lib/nginx/logs/error.log \
    && chmod 0644 /etc/nginx/nginx.conf /etc/nginx/conf.d/default.conf \
    && chown -R nginx:nginx /tmp/client_temp /tmp/proxy_temp /tmp/fastcgi_temp \
        /tmp/uwsgi_temp /tmp/scgi_temp /usr/share/nginx/html /etc/nginx/conf.d \
        /var/lib/nginx

USER 101:101

EXPOSE 8080 8443

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/health/live || exit 1

CMD ["nginx", "-g", "daemon off;"]
