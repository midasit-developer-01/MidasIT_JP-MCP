# MIDAS NX MCP server — remote (streamable-http) container image.
# Pull the base from AWS's ECR Public mirror of Docker Hub's official images
# (public.ecr.aws/docker/library/*) instead of Docker Hub directly. CodeBuild's
# shared egress IPs blow past Docker Hub's anonymous pull limit (429 Too Many
# Requests); ECR Public has no such limit from within AWS.
FROM public.ecr.aws/docker/library/python:3.12-slim

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
COPY pyproject.toml README.md constraints.txt ./
COPY midas_mcp ./midas_mcp
COPY data ./data
# temp endpoints are not officially released. External (pr) builds pass
# INCLUDE_TEMP=false (the default), which strips the temp schemas here BEFORE the
# wheel is built, so they never ship in the image. It also makes server.py skip
# registering midas_temp (it registers only when temp schemas are bundled), so
# the tool is absent too — no discovery, no call surface. Internal (dev) builds
# pass INCLUDE_TEMP=true to keep them.
ARG INCLUDE_TEMP=false
RUN [ "$INCLUDE_TEMP" = "true" ] || rm -rf ./data/schemas/temp
# Install against the lock file so the container gets the exact versions verified
# locally (transitive deps included), not whatever is newest at build time. This
# is what stops a drift like mcp 2.0 (which dropped mcp.server.fastmcp) from
# silently landing in the image. Update the lock with: pip freeze > constraints.txt
RUN pip install . -c constraints.txt

# App Runner / ALB health checks and MCP traffic hit this port.
EXPOSE 8080

# No key baked in — each request carries its own MAPI-Key header.
CMD ["midas-mcp"]
