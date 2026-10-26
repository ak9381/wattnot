"""Host identity for the capability probe.

Provider detection uses signals that exist inside a rental or a hosted
container. A laptop that only holds an API token stays "local".
"""

from __future__ import annotations

import os
import socket
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version

PROVIDERS = ("modal", "baseten", "e2e", "jarvislabs", "local")

# Present inside a Modal container. MODAL_TOKEN_ID and MODAL_ENVIRONMENT are
# also set on a developer machine, so they do not count.
_MODAL_CONTAINER_KEYS = ("MODAL_TASK_ID", "MODAL_IMAGE_ID", "MODAL_CLOUD_PROVIDER")

# Present inside a Baseten container. BASETEN_API_KEY is a client secret.
_BASETEN_CONTAINER_KEYS = ("BT_MODEL_ID", "BT_MODEL_DEPLOYMENT_ID")


def detect_provider(
    env: Mapping[str, str] | None = None,
    hostname: str | None = None,
) -> str:
    """Return modal, baseten, e2e, jarvislabs, or local.

    `WATTNOT_PROVIDER` wins when it names one of those. Anything else is ignored
    and detection falls through, so a typo does not hide a real container.
    """
    values = os.environ if env is None else env
    override = values.get("WATTNOT_PROVIDER", "").strip().lower()
    if override in PROVIDERS:
        return override

    in_modal = values.get("MODAL_IS_REMOTE") == "1" or any(
        values.get(key) for key in _MODAL_CONTAINER_KEYS
    )
    if in_modal:
        return "modal"
    if any(values.get(key) for key in _BASETEN_CONTAINER_KEYS):
        return "baseten"

    host = hostname if hostname is not None else socket.gethostname()
    lowered = host.lower()
    if "jarvislabs" in lowered:
        return "jarvislabs"
    if "e2enetworks" in lowered or lowered.startswith("e2e-"):
        return "e2e"
    return "local"


def detect_vllm_version() -> str | None:
    """Installed vLLM version, or None when the package is absent.

    Reads package metadata only. Importing vLLM itself can touch a GPU.
    """
    try:
        return package_version("vllm")
    except PackageNotFoundError:
        return None


def cuda_version_string(coded: int) -> str:
    """Decode NVML's CUDA driver version (1000 * major + 10 * minor)."""
    major = coded // 1000
    minor = (coded % 1000) // 10
    return f"{major}.{minor}"
