# Convenience targets. Run `make help` for the list.
.PHONY: help env infra up down logs ps topics psql build-java run-agent run-mcp run-ui clean

help:        ## Show this help
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

env:         ## Create .env from the template (does not overwrite)
	@test -f .env || cp .env.example .env

infra: env   ## Start infrastructure only (Postgres, Kafka, Redis, observability)
	docker compose up -d

up: env      ## Start infrastructure + all app containers
	docker compose --profile apps up -d --build

down:        ## Stop everything (keeps volumes)
	docker compose --profile apps down

clean:       ## Stop everything and DELETE volumes (resets the database)
	docker compose --profile apps down -v

logs:        ## Tail all logs
	docker compose --profile apps logs -f --tail=100

ps:          ## Show container status
	docker compose --profile apps ps

topics:      ## List Kafka topics
	docker compose exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:19092 --list

psql:        ## Open a psql shell
	docker compose exec postgres psql -U ecommerce -d ecommerce

build-java:  ## Build all Spring Boot services
	cd spring-boot-services && mvn clean package -DskipTests

run-agent:   ## Run agent-host locally
	cd agent-host && uvicorn app.main:app --reload --port 8092

run-mcp:     ## Run the ecommerce MCP server locally
	cd mcp-servers/ecommerce-mcp-server && python -m app.main

run-ui:      ## Run Streamlit locally
	cd streamlit-ui && streamlit run app.py
