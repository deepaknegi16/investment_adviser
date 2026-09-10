# Build the SPA, then hand the static output to nginx — the app server never
# serves assets.
FROM node:20-slim AS build
WORKDIR /src
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM alpine:3.20 AS dist
COPY --from=build /src/dist /dist
