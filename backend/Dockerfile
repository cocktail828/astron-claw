# ── Build stage ──────────────────────────────────────────────────────────────
FROM golang:1.25-alpine AS builder

RUN apk add --no-cache git

WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /server ./cmd/server

# ── Final stage ─────────────────────────────────────────────────────────────
FROM alpine:3.19

RUN apk add --no-cache ca-certificates tzdata curl && \
    adduser -D -u 1001 appuser

WORKDIR /app
COPY --from=builder /server /app/server
COPY --from=builder /app/.env.example /app/.env.example

RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://127.0.0.1:8765/api/health || exit 1

ENTRYPOINT ["/app/server"]
