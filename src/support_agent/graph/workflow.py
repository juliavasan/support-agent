"""LangGraph workflow:

START → retrieve_incident → format_incident → retrieve_context → analyze_incident
      → apply_customer_policy → write_work_note → END

retrieve_context searches the KB, known issues, and past incidents (retrieval/).
analyze_incident is the LLM step; apply_customer_policy is a deterministic rule
(premium customers get a higher recommended priority, see analysis/customer_tiers.py).

`graph` (module level) is the entry point referenced by langgraph.json for
`langgraph dev` / LangSmith Studio. Runs are dry-run unless the input sets
mode="write", and writes are limited to internal work notes on designated test
incidents (see guardrails/write_policy.py).
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from support_agent.analysis.analyzer import IncidentAnalyzer
from support_agent.analysis.customer_tiers import CustomerTiers, load_customer_tiers
from support_agent.analysis.priority import load_matrix
from support_agent.config import AgentSettings, load_agent_settings, load_servicenow_settings
from support_agent.graph.nodes import (
    Analyzer,
    AnalyzerFactory,
    ClientFactory,
    ContextRetriever,
    RetrieverFactory,
    SettingsFactory,
    TiersFactory,
    format_incident,
    make_analyze_incident,
    make_apply_customer_policy,
    make_retrieve_context,
    make_retrieve_incident,
    make_write_work_note,
)
from support_agent.graph.state import IncidentState, InputState, OutputState
from support_agent.retrieval import (
    PgSearchIndex,
    Reranker,
    RetrievalService,
    SearchBackend,
    SearchIndex,
    VoyageEmbedder,
)
from support_agent.servicenow import ServiceNowClient

# Settings and clients are created when a node runs, not at import, so Studio can load
# the graph before credentials are configured.


def default_client_factory() -> ServiceNowClient:
    return ServiceNowClient(load_servicenow_settings())


def default_settings_factory() -> AgentSettings:
    return load_agent_settings()


def default_analyzer_factory() -> Analyzer:
    settings = load_agent_settings()
    return IncidentAnalyzer(settings, load_matrix(settings.priority_matrix_path))


def default_index_factory(settings: AgentSettings) -> SearchBackend:
    """Hybrid Postgres + pgvector index when configured, else the offline SQLite index."""
    if settings.retrieval_database_url:
        embedder = VoyageEmbedder(settings.embedding_model, settings.embedding_dimensions)
        return PgSearchIndex(settings.retrieval_database_url, embedder)
    return SearchIndex(settings.retrieval_db_path)


def default_retriever_factory() -> ContextRetriever:
    settings = load_agent_settings()
    return RetrievalService(
        index=default_index_factory(settings),
        reranker=Reranker(settings),
        threshold=settings.relevance_threshold,
        sync_client=default_client_factory() if settings.retrieval_sync_on_run else None,
    )


def default_tiers_factory() -> CustomerTiers:
    return load_customer_tiers(load_agent_settings().customer_tiers_path)


def build_graph(
    client_factory: ClientFactory = default_client_factory,
    analyzer_factory: AnalyzerFactory = default_analyzer_factory,
    settings_factory: SettingsFactory = default_settings_factory,
    tiers_factory: TiersFactory = default_tiers_factory,
    retriever_factory: RetrieverFactory = default_retriever_factory,
) -> CompiledStateGraph[IncidentState, None, InputState, OutputState]:
    builder = StateGraph(IncidentState, input_schema=InputState, output_schema=OutputState)
    builder.add_node("retrieve_incident", make_retrieve_incident(client_factory))
    builder.add_node("format_incident", format_incident)
    builder.add_node("retrieve_context", make_retrieve_context(retriever_factory))
    builder.add_node("analyze_incident", make_analyze_incident(analyzer_factory))
    builder.add_node("apply_customer_policy", make_apply_customer_policy(tiers_factory))
    builder.add_node("write_work_note", make_write_work_note(client_factory, settings_factory))
    builder.add_edge(START, "retrieve_incident")
    builder.add_edge("retrieve_incident", "format_incident")
    builder.add_edge("format_incident", "retrieve_context")
    builder.add_edge("retrieve_context", "analyze_incident")
    builder.add_edge("analyze_incident", "apply_customer_policy")
    builder.add_edge("apply_customer_policy", "write_work_note")
    builder.add_edge("write_work_note", END)
    return builder.compile()


graph = build_graph()
