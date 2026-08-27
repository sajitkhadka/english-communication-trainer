# The relay serves the *same* Vite build the PC serves (ADR 0006 §1), so the frontend
# is built here rather than shipped separately - one image, one version, no chance of
# the recorder and the app drifting apart.
#
#   docker build -f relay/Dockerfile -t 192.168.0.120:5000/ect-relay:<tag> .
#
# Build context is the repo root, not relay/, because of that frontend stage.

# --- 1. the frontend --------------------------------------------------------
FROM node:24-alpine AS web
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build

# --- 2. the relay binary ----------------------------------------------------
FROM golang:1.25-alpine AS build
WORKDIR /src
COPY relay/go.mod relay/go.sum ./
RUN go mod download
COPY relay/ ./
# CGO_ENABLED=0 keeps it a static binary: modernc.org/sqlite is pure Go, which is
# exactly why it was chosen over mattn/go-sqlite3.
ENV CGO_ENABLED=0
RUN go build -trimpath -ldflags="-s -w" -o /out/relay .

# --- 3. the image ------------------------------------------------------------
FROM gcr.io/distroless/static-debian12:nonroot

COPY --from=build /out/relay /usr/local/bin/relay
COPY --from=web /src/dist /srv/web

ENV ECT_RELAY_ADDR=:8080 \
    ECT_RELAY_STATIC_DIR=/srv/web \
    ECT_RELAY_DATA_DIR=/data

# /data is a PVC in k8s: it holds inbox.db, the blobs still waiting for the PC, and
# the digest snapshot. Losing it loses undrained recordings, so it is not scratch.
VOLUME ["/data"]
EXPOSE 8080
USER nonroot:nonroot
ENTRYPOINT ["/usr/local/bin/relay"]
