"""LangGraph Studio configuration and connectivity.

Offline tests validate langgraph.json and that the referenced graph loads.
The `live` test checks a running `langgraph dev` server (opt-in: pytest -m live).
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
DEV_SERVER = os.environ.get("LANGGRAPH_DEV_URL", "http://127.0.0.1:2024")


def load_config() -> dict:  # type: ignore[type-arg]
    return json.loads((ROOT / "langgraph.json").read_text())


def test_langgraph_json_is_valid() -> None:
    config = load_config()
    assert config["dependencies"] == ["."]
    assert config["env"] == ".env"
    assert "incident_workflow" in config["graphs"]


def test_graph_path_resolves_to_compiled_graph() -> None:
    file_part, attr = load_config()["graphs"]["incident_workflow"].split(":")
    spec = importlib.util.spec_from_file_location("studio_graph", ROOT / file_part)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    graph = getattr(module, attr)
    assert {"retrieve_incident", "format_incident"} <= set(graph.get_graph().nodes)


def test_graph_input_schema_is_incident_id_only() -> None:
    from support_agent.graph.workflow import graph

    schema = graph.get_input_jsonschema()
    assert set(schema["properties"]) == {"incident_id"}


@pytest.mark.live
def test_dev_server_exposes_graph() -> None:
    """Requires `langgraph dev` to be running."""
    assert requests.get(f"{DEV_SERVER}/ok", timeout=5).status_code == 200
    assistants = requests.post(
        f"{DEV_SERVER}/assistants/search", json={"graph_id": "incident_workflow"}, timeout=5
    ).json()
    assert assistants, "incident_workflow graph not registered on the dev server"
