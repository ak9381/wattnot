"""Efficiency certificates built from verified receipts.

Each certificate restates one receipt's own figures and is labelled by that receipt's evidence
(MEASURED for a measured run). It is not tradable, not a carbon credit, and no registry accepts
it, and it says so on its face. The watermark is part of the hashed document and the renderer has
no switch to remove it. An intact receipt whose saving is not statistically shown is declined
(a "not issued" statement), not minted.
"""

from wattnot.certificate.render import render_html
from wattnot.certificate.rules import (
    CERT_FORMAT,
    DECLINE_FORMAT,
    REGISTER_FORMAT,
    WATERMARK_LINES,
    MintRefused,
    build_declined,
    declinable,
    decline_doc_for,
    mint,
    verify_certificate,
    verify_register,
)

__all__ = ["CERT_FORMAT", "DECLINE_FORMAT", "REGISTER_FORMAT", "WATERMARK_LINES", "MintRefused",
           "build_declined", "declinable", "decline_doc_for", "mint", "render_html",
           "verify_certificate", "verify_register"]
