FROM python:3.11.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system adapter && useradd --system --gid adapter --home-dir /nonexistent adapter

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir .

USER adapter
EXPOSE 8000
ENTRYPOINT ["leoai-mcp-adapter"]
