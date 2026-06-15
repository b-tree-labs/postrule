# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Shared TLS trust store for the SDK's stdlib HTTP calls.

Every ``urllib.request.urlopen`` in the SDK reaches an HTTPS endpoint
(the hosted API, the dashboard, insights, dataset mirrors). The stdlib
*default* SSL context relies on the platform trust store, which is
**absent or unpopulated** on two extremely common setups:

- macOS Python from the python.org installer (no system roots wired in
  unless the user ran *Install Certificates.command*), and
- slim / distroless container images shipped without ``ca-certificates``.

On those, every HTTPS call raises ``ssl.SSLCertVerificationError``
(``CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate``).
For the always-on, fail-silent telemetry pipe that meant **every verdict
silently dropped** and the customer never learned their metering feed was
dead; for ``postrule verify`` / ``login`` / insights it meant an opaque
failure on a fresh machine.

The fix: route every SDK HTTPS call through one verifying context backed
by :mod:`certifi`'s CA bundle when it is importable (it ships transitively
with the vast majority of installs via requests / httpx / google libs),
falling back to the stdlib default otherwise — which still honours the
``SSL_CERT_FILE`` / ``SSL_CERT_DIR`` environment variables.

``certifi`` stays a **soft** dependency: used when present, never required,
so ``pip install postrule`` stays lean. Verification is always ON
(``CERT_REQUIRED`` + hostname check). We fix the trust store; we never
bypass it.
"""

from __future__ import annotations

import ssl
from typing import Final


def _build_ssl_context() -> ssl.SSLContext:
    """Build a verifying TLS context backed by certifi when available."""
    try:
        import certifi  # soft dependency — most installs already have it

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # certifi missing or unreadable bundle
        return ssl.create_default_context()


#: Built once at import (cheap, no network) and reused. ``SSLContext`` is safe
#: to share across threads for client connections, so a single module-level
#: instance serves every ``urlopen`` call in the SDK.
SSL_CONTEXT: Final[ssl.SSLContext] = _build_ssl_context()


def ssl_context() -> ssl.SSLContext:
    """Return the shared verifying TLS context for SDK HTTPS calls.

    Pass to ``urllib.request.urlopen(..., context=ssl_context())`` so the
    handshake verifies against a real CA bundle on every platform.
    """
    return SSL_CONTEXT
