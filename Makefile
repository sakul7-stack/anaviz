# anaviz — root orchestrator for two INDEPENDENT projects.
#
#   anaviz/       the visualization app        (make -C anaviz ...)
#   datasource/   the example HLT data API     (make -C datasource ...)
#
# Each project has its own docker-compose.yml, Dockerfile, .dockerignore and
# Makefile and can be run entirely on its own. This root Makefile is only a
# convenience that starts them in a sensible order on a shared network.
#
# Data safety: both projects bind their postgres volumes as EXTERNAL to the
# existing physical volumes, so `up`/`down` NEVER re-ingest or wipe data.

.PHONY: help net up down datasource anaviz ps logs

help:            ## Show these targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-12s %s\n", $$1, $$2}'
	@echo "  (per-project: make -C anaviz help / make -C datasource help)"

net:             ## Create the shared discovery network (idempotent)
	@docker network create anaviz_shared >/dev/null 2>&1 || true

datasource: net  ## Start the datasource project only
	$(MAKE) -C datasource up

anaviz: net      ## Start the anaviz project only
	$(MAKE) -C anaviz up

up: datasource anaviz   ## Start both projects (datasource first, then anaviz)
	@echo "up: datasource :9000 (DB :5434) + anaviz :8000 (cache :5433)"

down:            ## Stop both projects (volumes retained)
	-$(MAKE) -C anaviz down
	-$(MAKE) -C datasource down

ps:              ## Status of both projects' containers
	@docker ps --filter name=anaviz-project --filter name=anaviz-datasource

logs:            ## Follow logs from both containers
	@docker logs -f anaviz-project & docker logs -f anaviz-datasource
