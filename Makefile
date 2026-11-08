# Convenience targets. Run `make help` for the list.
.PHONY: eval-rag eval-rag-holdout help env infra up down logs ps topics psql build-java run-agent test-agent run-mcp test-mcp run-ui clean

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

run-agent:   ## Run agent-host locally (needs Python 3.10+ and its own venv)
	cd agent-host && PYTHONPATH=. uvicorn app.main:app --reload --port 8092

test-agent:  ## Run agent-host tests
	cd agent-host && PYTHONPATH=. python -m pytest -q

run-mcp:     ## Run the ecommerce MCP server locally (needs Python 3.10+)
	cd mcp-servers/ecommerce-mcp-server && PYTHONPATH=..:. python -m app.main

test-mcp:    ## Run MCP server tests
	cd mcp-servers/ecommerce-mcp-server && PYTHONPATH=..:. python -m pytest -q
	cd mcp-servers/policy-mcp-server && PYTHONPATH=..:. python -m pytest -q

run-ui:      ## Run Streamlit locally
	cd streamlit-ui && streamlit run app.py

PY ?= python3   # needs httpx, e.g. make eval-rag PY=.venv-agent/bin/python

eval-rag:    ## RAG quality: retrieval + answers + ingestion lifecycle (stack running, model key set)
	$(PY) evaluation/rag/run_retrieval_eval.py
	$(PY) evaluation/rag/run_answer_eval.py
	$(PY) evaluation/rag/ingestion_lifecycle_check.py

eval-rag-holdout: ## RAG quality on unseen questions (informational)
	$(PY) evaluation/rag/run_answer_eval.py --set golden_holdout --no-poison
