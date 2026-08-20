# anaviz PROJECT — standalone project targets.
#
# The anaviz project is the dataset-independent viz app (:8000) with its own
# generic cache DB (host :5433). It contains ZERO HLT code and runs on its own;
# the datasource is just one example source it can be pointed at.
#
# Its cache volume is external (bound to the existing cache). `up`/`down` never
# destroy real source data — the source of truth lives in the datasource
# project, and the cache re-hydrates on demand anyway.

COMPOSE := docker compose -f docker-compose.yml

.PHONY: help net up down restart logs ps psql clear-cache build test

help:            ## Show these targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-12s %s\n", $$1, $$2}'

net:             ## Ensure the shared discovery network exists
	@docker network create anaviz_shared >/dev/null 2>&1 || true

up: net          ## Build + start the project (:8000, cache DB :5433)
	$(COMPOSE) up -d --build

down:            ## Stop the project (cache volume retained)
	$(COMPOSE) down

restart:         ## Recreate the project container (cache kept)
	$(COMPOSE) up -d --build --force-recreate

build:           ## Build the project image only
	$(COMPOSE) build

logs:            ## Follow project logs
	$(COMPOSE) logs -f

ps:              ## Project container status
	$(COMPOSE) ps

psql:            ## psql into the project cache DB
	$(COMPOSE) exec project psql -U dcs -d dcs

clear-cache:     ## Flush the project cache (source data untouched)
	curl -fsS http://localhost:8000/api/clear-cache && echo

test:            ## Run offline unit tests (no DB needed)
	python -m pytest tests/ -q
