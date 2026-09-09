# Run-command wrappers. Infra (Dockerfiles, compose, watcher, .env) lives under infra/;
# these carry the -f / build context so the canonical commands stay short and correct.
# `docker compose -f infra/docker-compose.yml` sets the project dir to infra/, so infra/.env
# auto-loads (do NOT add --project-directory, which would re-point .env at the cwd).
COMPOSE := docker compose -f infra/docker-compose.yml

# Local run without Docker (stdlib only — no venv, no pip, no build). Override inline, e.g.
#   make run PORT=9000 MDREVIEW_DATA=$HOME/.mdreview
PORT ?= 8137
MDREVIEW_DATA ?= $(CURDIR)/data

DEV_PORT ?= 8138
DEV_DATA ?= $(CURDIR)/.scratch/dev-data

.PHONY: build up down smoke run dev dev-stop dev-smoke

build:    ## build the service image from the repo-root context
	docker build -f infra/Dockerfile -t mdreview-service .

up:       ## build + run the service on localhost:8137
	$(COMPOSE) up -d --build

down:     ## stop the service
	$(COMPOSE) down

smoke:    ## healthcheck the running service
	curl -fsS localhost:8137/healthz && echo

run:      ## run the service locally WITHOUT Docker (PYTHONPATH=src; data in ./data, gitignored)
	PYTHONPATH=src MDREVIEW_DATA=$(MDREVIEW_DATA) PORT=$(PORT) python3 -m mdreview

# The sandbox for developing mdreview-service itself: a background instance on its own port and
# data dir, distinct from the daily-driver (`make up`/an installed `mdreview` MCP alias) so editing
# src/ here can never break that alias. Wire a separate `mdreview-dev` MCP alias at this port:
#   claude mcp add mdreview-dev -e MDREVIEW_BASE=http://localhost:8138 -- python3 src/mcp_server.py
dev:      ## run a background dev instance on localhost:8138 (data in .scratch/dev-data, gitignored)
	@mkdir -p $(DEV_DATA)
	PYTHONPATH=src MDREVIEW_DATA=$(DEV_DATA) PORT=$(DEV_PORT) nohup python3 -m mdreview \
		> .scratch/dev-server.log 2>&1 & echo $$! > .scratch/dev-server.pid
	@sleep 0.5
	@curl -fsS localhost:$(DEV_PORT)/healthz > /dev/null && \
		echo "dev server up on $(DEV_PORT), pid $$(cat .scratch/dev-server.pid), log .scratch/dev-server.log"

dev-stop: ## stop the background dev instance
	@if [ -f .scratch/dev-server.pid ]; then \
		kill $$(cat .scratch/dev-server.pid) 2>/dev/null || true; \
		rm -f .scratch/dev-server.pid; echo "dev server stopped"; \
	else echo "no .scratch/dev-server.pid — nothing to stop"; fi

dev-smoke: ## healthcheck the dev instance
	curl -fsS localhost:$(DEV_PORT)/healthz && echo
