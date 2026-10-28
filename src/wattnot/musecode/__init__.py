"""Muse Code integration package for WattNot.

Provides PR application and framework snippet generation / validation.
"""

from __future__ import annotations

from wattnot.musecode.apply_pr import ApplyPRResult, apply_pr
from wattnot.musecode.snippets import SnippetResult, generate_and_validate_snippets

__all__ = [
    "ApplyPRResult",
    "SnippetResult",
    "apply_pr",
    "generate_and_validate_snippets",
]
