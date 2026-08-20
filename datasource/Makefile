# anaviz DATASOURCE — standalone project targets.
#
# The datasource owns the real HLT data (TimescaleDB) and serves /archive/* on
# :9000 (DB on host :5434). It is independent of the anaviz project.
#
# Its postgres volume is external (bound to the existing ingested data), so
# `up`/`down` NEVER re-ingest. Only `docker volume rm anaviz_anaviz_datasource_pgdata`
# would (don't do that unless you mean it).

COMPOSE := docker compose -f docker-compose.yml
SRC_DB  := postgresql://dcs:dcs@localhost:5434/dcs

.PHONY: help net up down restart logs ps psql download ingest build

help:            ## Show these targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-10s %s\n", $$1, $$2}'

net:             ## Ensure the shared discovery network exists
	@docker network create anaviz_shared >/dev/null 2>&1 || true

up: net          ## Build + start the datasource (:9000, DB :5434)
	$(COMPOSE) up -d --build

down:            ## Stop the datasource (data volume retained)
	$(COMPOSE) down

restart:         ## Recreate the datasource container (volume kept)
	$(COMPOSE) up -d --build --force-recreate

build:           ## Build the datasource image only
	$(COMPOSE) build

logs:            ## Follow datasource logs
	$(COMPOSE) logs -f

ps:              ## Datasource container status
	$(COMPOSE) ps

psql:            ## psql into the datasource DB (real HLT data)
	$(COMPOSE) exec datasource psql -U dcs -d dcs

download:        ## Download the HLT CSVs into data/hlt (resumable)
	python -m anaviz_datasource download --out data/hlt

ingest:          ## Ingest data/hlt CSVs INTO THE DATASOURCE DB (:5434)
	python -m anaviz_datasource ingest --data-dir data/hlt --files all --db-url $(SRC_DB)
