# anaviz — launch targets
#
# The stack has two containers you can start together or separately:
#
#   datasource  : real HLT data + /archive/* API (:9000, postgres :5434)
#   project     : the viz app (:8000, cache postgres :5433)
#
# `make app-up` starts the project and, because compose tracks its dependency,
# also starts the datasource if it isn't running. Use `--no-deps` (or just
# `make db-up` first, then `make app-up`) to start the project alone.

.PHONY: db-up app-up up down ps logs db-shell app-shell rebuild help

help:              ## Show these targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-12s %s\n", $$1, $$2}'

db-up:             ## Launch ONLY the datasource database container (:9000, :5434)
	sudo docker compose up -d --build datasource

app-up:            ## Launch ONLY the project container (:8000, :5433)
	sudo docker compose up -d --build project

up:                ## Launch both containers
	sudo docker compose up -d --build

down:              ## Stop both containers (volumes retained)
	sudo docker compose down

ps:                ## Container status
	sudo docker compose ps

logs:              ## Follow logs from both containers
	sudo docker compose logs -f

db-shell:          ## psql into the datasource DB (real data)
	sudo docker compose exec datasource psql -U dcs -d dcs

app-shell:         ## psql into the project cache DB
	sudo docker compose exec project psql -U dcs -d dcs

rebuild:           ## Rebuild images and recreate containers (volumes kept)
	sudo docker compose up -d --build --force-recreate
