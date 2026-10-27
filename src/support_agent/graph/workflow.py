"""LangGraph workflow: START → retrieve_incident → format_incident → END.

`graph` (module level) is the entry point referenced by langgraph.json for
`langgraph dev` / LangSmith Studio. Phase 1 is read-only and calls no LLM.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from support_agent.config import load_servicenow_settings
from support_agent.graph.nodes import ClientFactory, format_incident, make_retrieve_incident
from support_agent.graph.state import IncidentState, InputState, OutputState
from support_agent.servicenow import ServiceNowClient


def default_client_factory() -> ServiceNowClient:
    # Settings are read when the node runs, not at import, so Studio can load the
    # graph even before credentials are configured.
    return ServiceNowClient(load_servicenow_settings())


def build_graph(client_factory: ClientFactory = default_client_factory) -> CompiledStateGraph:
    builder = StateGraph(IncidentState, input_schema=InputState, output_schema=OutputState)
    builder.add_node("retrieve_incident", make_retrieve_incident(client_factory))
    builder.add_node("format_incident", format_incident)
    builder.add_edge(START, "retrieve_incident")
    builder.add_edge("retrieve_incident", "format_incident")
    builder.add_edge("format_incident", END)
    return builder.compile()


graph = build_graph()
