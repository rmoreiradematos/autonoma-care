FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY pyproject.toml README.md ./
COPY app ./app
COPY mcp_server ./mcp_server
COPY scripts ./scripts
RUN pip install --no-cache-dir . && mkdir -p /app/data

EXPOSE 8091 8765
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8091"]
