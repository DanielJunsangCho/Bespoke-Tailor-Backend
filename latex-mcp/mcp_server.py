from fastmcp import FastMCP
import os

mcp = FastMCP("latex_provider")


# Load LaTeX plugin
from plugins.latex import register as register_latex
register_latex(mcp)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 0))
    mcp.run(transport="http", host="0.0.0.0", port=port, path="/mcp")
