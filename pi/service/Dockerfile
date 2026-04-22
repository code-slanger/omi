FROM python:3.11-slim

# System deps for librosa (audio processing) and chromadb
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    ffmpeg \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user matching host lucho uid/gid (1000:1000)
RUN groupadd -g 1000 appuser && useradd -u 1000 -g 1000 -m appuser

# Install uv into a system-wide location
RUN curl -Ls https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh

WORKDIR /app
RUN chown appuser:appuser /app

USER appuser

COPY --chown=appuser:appuser corpus /corpus
COPY --chown=appuser:appuser pi/service/pyproject.toml ./
RUN sed -i 's|corpus @ [^"]*corpus|corpus @ file:///corpus|g' pyproject.toml
RUN uv sync --no-dev

COPY --chown=appuser:appuser pi/service/src/ src/

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "src.personalisation.main:app", "--host", "0.0.0.0", "--port", "8000"]
