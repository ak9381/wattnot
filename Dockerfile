# Hosted replay of a saved WattNot run, for judges. No GPU, no model calls, no keys.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WATTNOT_HOSTED_REPLAY=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
# Editable, so the server finds web/ and data/ next to src/ (it resolves them from the source tree).
RUN pip install --no-cache-dir -e . \
    && useradd --create-home --uid 10001 wattnot

COPY web/ ./web/
COPY data/dash/ ./data/dash/
COPY docs/dashboard.md ./docs/dashboard.md
COPY schemas/ ./schemas/

USER wattnot

EXPOSE 8765

# The saved real run, when mounted at /app/saved.
CMD if [ -f /app/saved/run.events.jsonl ]; then EVENTS="--events /app/saved/run.events.jsonl"; fi; \
    exec python -m wattnot.dash --host 0.0.0.0 --port 8765 --replay-only ${EVENTS}
