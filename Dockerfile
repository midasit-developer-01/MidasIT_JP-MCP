# MIDAS NX MCP server — remote (streamable-http) container image.
FROM python:3.12-slim

# Faster, quieter, unbuffered logs (good for container stdout).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    MIDAS_MCP_TRANSPORT=streamable-http \
    MIDAS_MCP_HOST=0.0.0.0 \
    MIDAS_MCP_PORT=8080

WORKDIR /app

# Copy metadata + source, then install. `pip install .` builds the wheel via
# hatchling and bundles data/ (force-include in pyproject.toml).
COPY pyproject.toml README.md ./
COPY midas_mcp ./midas_mcp
COPY data ./data
RUN pip install .

# App Runner / ALB health checks and MCP traffic hit this port.
EXPOSE 8080

# No key baked in — each request carries its own MAPI-Key header.
CMD ["midas-mcp"]
