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

# Open the dashboard on the bundled replay. No GPU, no network, no key.
# Needs WattNot installed (pip install -e .); run scripts/setup.sh first if not.
demo:
	python -m wattnot.cli demo
