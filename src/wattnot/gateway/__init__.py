"""WattNot gateway: the OpenAI-compatible proxy with every tested saver, built from one YAML."""
from wattnot.gateway.build import build_app, load_config

__all__ = ["build_app", "load_config"]
