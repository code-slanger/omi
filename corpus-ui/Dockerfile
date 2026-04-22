FROM python:3.11-slim

WORKDIR /app

RUN pip install uv

# Build context must be omi/ root (see docker-compose.yml)
COPY corpus /corpus
COPY corpus-ui /app

RUN sed -i 's|corpus @ [^"]*corpus|corpus @ file:///corpus|g' pyproject.toml
RUN uv pip install --system --no-cache ".[full]"

EXPOSE 8001

CMD ["uvicorn", "corpus_ui.main:app", "--host", "0.0.0.0", "--port", "8001"]
