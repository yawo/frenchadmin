from typing import Generator

from database.database_manage import get_connection
from database.graph_manage import _get_graph


def get_db() -> Generator:
    """FastAPI dependency for PostgreSQL connection from the existing pool."""
    with get_connection() as conn:
        yield conn


def get_graph():
    """FastAPI dependency for the FalkorDB graph instance."""
    graph = _get_graph()
    if graph is None:
        raise RuntimeError("FalkorDB is not available")
    return graph


def get_graph_optional():
    """Returns the graph or None if unavailable (non-fatal)."""
    return _get_graph()
