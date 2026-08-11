"""Entry point for `python -m tfqa`.

The MCP server runs the CLI as a subprocess and cannot rely on the `tfqa`
console script being on PATH -- inside a virtual environment invoked by an
agent, often it is not. `sys.executable -m tfqa` always resolves to the same
interpreter the server is running under.
"""

from tfqa.cli.main import app

if __name__ == "__main__":
    app()
