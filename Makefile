.PHONY: test lint demo rehearse-run3

# Offline unit tests. No network, no GPU, no Meta API.
test:
	python -m pytest

# Full RUN3 rehearsal on CPU with mock NVML/GPU/hosted backends. No GPU, no network, no secrets.
rehearse-run3:
	bash scripts/rehearse_run3.sh

# Static checks.
lint:
	python -m ruff check .

# Placeholder until the demo lands (see later tasks).
demo:
	@echo "wattnot demo: not built yet. Run 'make test' and 'make lint' for now."
