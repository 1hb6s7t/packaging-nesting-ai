FROM node:24-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json /app/
RUN npm ci
COPY frontend /app
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
