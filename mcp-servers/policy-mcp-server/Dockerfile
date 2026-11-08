# Build context is the mcp-servers/ folder so the shared mcp_common package is available:
#   docker build -f policy-mcp-server/Dockerfile mcp-servers
FROM python:3.12-slim
WORKDIR /srv
COPY policy-mcp-server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mcp_common ./mcp_common
COPY policy-mcp-server/app ./app
ENV PYTHONPATH=/srv PYTHONUNBUFFERED=1
EXPOSE 8091
CMD ["python", "-m", "app.main"]
