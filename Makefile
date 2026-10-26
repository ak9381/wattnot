.PHONY: test lint demo

# Offline unit tests. No network, no GPU, no Meta API.
test:
	python -m pytest

# Static checks.
lint:
	python -m ruff check .

# Placeholder until the demo lands (see later tasks).
demo:
	@echo "wattnot demo: not built yet. Run 'make test' and 'make lint' for now."
