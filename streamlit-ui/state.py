"""Session state helpers and the (replaceable) API client."""
from __future__ import annotations

import streamlit as st

from api_client import ApiClient, Identity


@st.cache_resource
def _default_api() -> ApiClient:
    return ApiClient()


def get_api():
    """The shared API client. Tests put a fake in st.session_state['_api_override']."""
    return st.session_state.get("_api_override") or _default_api()


@st.cache_resource
def _default_logs():
    from log_source import LogSource
    return LogSource()


def get_logs():
    """Reads the other containers' logs. Tests put a fake in st.session_state['_logs_override']."""
    return st.session_state.get("_logs_override") or _default_logs()


def identity() -> Identity:
    return st.session_state["identity"]


def open_workflow(workflow_id: str) -> None:
    st.session_state["workflow_id"] = workflow_id.strip()


def selected_workflow() -> str | None:
    return st.session_state.get("workflow_id") or None
