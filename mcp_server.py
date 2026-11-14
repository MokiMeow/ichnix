"""Standalone local MCP stdio launcher."""
from ichnix.graph import Graph
from ichnix.mcp_server import serve


if __name__ == "__main__":
    serve(Graph)
