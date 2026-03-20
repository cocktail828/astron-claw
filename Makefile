# ── Astron Claw — Development & Build ────────────────────────────────────────

.PHONY: dev dev-web dev-server preview build-web install clean

# ── One-click development (frontend + backend) ──────────────────────────────
dev:
	@echo "🚀 Starting Astron Claw (dev mode)..."
	@trap 'kill 0' EXIT; \
		$(MAKE) dev-server & \
		$(MAKE) dev-web & \
		wait

# ── Frontend dev server ─────────────────────────────────────────────────────
dev-web:
	cd web && pnpm dev --host

# ── Backend dev server ──────────────────────────────────────────────────────
dev-server:
	cd backend && go run ./cmd/server

# ── Build frontend for production ───────────────────────────────────────────
build-web:
	cd web && pnpm build

# ── Preview: build + serve bundled files (fast remote access) ───────────────
preview:
	@echo "🚀 Starting Astron Claw (preview mode)..."
	@cd web && pnpm build
	@trap 'kill 0' EXIT; \
		$(MAKE) dev-server & \
		cd web && pnpm preview --host --port 5173 & \
		wait

# ── Install all dependencies ────────────────────────────────────────────────
install:
	cd web && pnpm install
	cd backend && go mod download

# ── Clean build artifacts ───────────────────────────────────────────────────
clean:
	rm -rf web/dist web/node_modules/.vite
