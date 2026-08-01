# Stage 1: build the static React bundle
FROM node:20-slim AS build

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .

# Vite reads VITE_* vars at BUILD time, not runtime — this must be passed
# as a build arg, not a regular docker-compose `environment:` entry, or
# the frontend will silently bake in the wrong API URL
ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

# Stage 2: serve the built static files with nginx
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
