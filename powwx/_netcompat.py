"""Optional TLS compatibility for local runs behind a corporate proxy.

On networks that intercept TLS with a private root CA (common on corporate
networks), Python's bundled ``certifi`` bundle won't trust the connection and
``requests`` raises CERTIFICATE_VERIFY_FAILED — even though the OS trust store
has the root. ``truststore`` makes Python use the OS trust store instead.

This is a no-op on clean networks (e.g. GitHub Actions runners) and is skipped
silently if ``truststore`` isn't installed, so it never becomes a hard
dependency. Call :func:`enable` once at process start.
"""

from __future__ import annotations


def enable() -> bool:
    """Inject the OS trust store into ssl if truststore is available."""
    try:
        import truststore
    except ImportError:
        return False
    truststore.inject_into_ssl()
    return True
