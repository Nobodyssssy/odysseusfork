"""Tiny stdio MCP server for the lab: one tool that writes a file wherever it is told."""
from mcp.server.mcpserver import MCPServer

app = MCPServer('stubmcp')


@app.tool()
def write_note(path: str, text: str) -> str:
    """Write text to a file."""
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(text)
    return f'wrote {len(text)} bytes to {path}'


if __name__ == '__main__':
    app.run()
