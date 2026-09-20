# Multi-stage Dockerfile for React Owner Dashboard
# Stage 1: Build the static assets
FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

# Build production bundle (defaults VITE_API_BASE to /api)
RUN npm run build

# Stage 2: Serve with Nginx
FROM nginx:alpine

# Copy built production assets
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy custom Nginx reverse proxy configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
