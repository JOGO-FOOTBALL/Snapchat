FROM python:3.12.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential git && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Private package (auth_config / login.py use utils433.absUtils to read/write
# the streamlit-authenticator config in Azure Blob Storage). Kept out of
# pyproject.toml/uv.lock since it needs a GitHub token to fetch - installed
# separately here via a build secret instead.
RUN --mount=type=secret,id=github_token \
    uv pip install git+https://$(cat /run/secrets/github_token)@github.com/JOGO-FOOTBALL/utils433.git@v0.0.66


FROM python:3.12.14-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ca-certificates \
    ffmpeg && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/.venv ./.venv

COPY ./src ./src
COPY alembic.ini ./
COPY alembic ./alembic

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8501

CMD ["streamlit", "run", "src/streamlit_app.py", "--server.port=8501", "--server.address=0.0.0.0"]
