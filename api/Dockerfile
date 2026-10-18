FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.1 /uv /usr/local/bin/uv
WORKDIR /srv
# Dependencies first, at the exact versions in uv.lock, so a fresh build installs what was tested
# and a code change does not reinstall them. uv downloads in parallel and retries, with a longer
# timeout than pip's, so one slow download does not fail the build.
ENV UV_HTTP_TIMEOUT=120 UV_NO_CACHE=1
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --extra dev --no-emit-project --no-hashes -o /tmp/requirements.txt \
 && uv pip install --system -r /tmp/requirements.txt
COPY app ./app
RUN uv pip install --system --no-deps .
COPY migrations ./migrations
COPY tests ./tests
COPY seed.sql worker.py ./
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
