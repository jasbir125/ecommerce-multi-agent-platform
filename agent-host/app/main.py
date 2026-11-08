"""Agent host entry point. LangGraph supervisor + agents arrive in Phase 4."""
from fastapi import FastAPI

app = FastAPI(title="agent-host")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
