FROM python:3.11-slim

# System deps for librosa (audio processing) and chromadb
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    ffmpeg \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -Ls https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

COPY pyproject.toml ./
RUN uv sync --no-dev

COPY src/ src/

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "src.personalisation.main:app", "--host", "0.0.0.0", "--port", "8000"]
