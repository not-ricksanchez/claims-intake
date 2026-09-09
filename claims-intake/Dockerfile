FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
# Hatchling installs the local `claims` package; src/ must be present for `uv sync`.
COPY src ./src
RUN uv sync --frozen --no-dev
COPY data ./data
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "claims.api.routes:app", "--host", "0.0.0.0", "--port", "8000"]
