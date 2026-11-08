"""Thin HTTP client for the workflow / approval / audit APIs (Phase 6)."""
import os

API_BASE = os.getenv("STREAMLIT_API_BASE_URL", "http://localhost:8088")
APPROVAL_API_BASE = os.getenv("STREAMLIT_APPROVAL_API_BASE_URL", "http://localhost:8085")
