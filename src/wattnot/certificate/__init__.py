"""Mock efficiency certificates built from verified receipts.

Every certificate is a MOCK. It is not tradable, not a carbon credit, and no registry accepts it.
The watermark is part of the hashed document and the renderer has no switch to remove it.
"""

from wattnot.certificate.render import render_html
from wattnot.certificate.rules import (
    CERT_FORMAT,
    REGISTER_FORMAT,
    WATERMARK_LINES,
    MintRefused,
    mint,
    verify_certificate,
    verify_register,
)

__all__ = ["CERT_FORMAT", "REGISTER_FORMAT", "WATERMARK_LINES", "MintRefused", "mint",
           "render_html", "verify_certificate", "verify_register"]
