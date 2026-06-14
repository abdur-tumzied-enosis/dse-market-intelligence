"""
Shared TLS policy for the dse_direct adapter family.

www.dsebd.org serves an INCOMPLETE certificate chain: it presents only the
leaf cert (*.dsebd.org) and omits its issuing intermediate
("Sectigo Public Server Authentication CA DV R36"). Browsers and Windows hide
this by AIA-chasing (downloading the missing intermediate on the fly), but
OpenSSL — i.e. Python under Linux/Docker — does not, so every verifying
httpx client raises:

    [SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate

Fix: pin the one missing intermediate (certs/dsebd_intermediate.pem, fetched
from the cert's CA-Issuers URI http://crt.sectigo.com/...) on top of the
certifi root bundle. Verification stays ON — this is NOT verify=False — and it
works identically on Windows and in Docker.

If the cert chain changes (Sectigo rotates the intermediate), refresh the pem:

    URL=$(echo | openssl s_client -connect www.dsebd.org:443 \
            -servername www.dsebd.org 2>/dev/null \
          | openssl x509 -noout -text | grep -oP 'CA Issuers - URI:\\K\\S+')
    curl -s "$URL" | openssl x509 -inform DER \
          -out extraction/adapters/dse_direct/certs/dsebd_intermediate.pem
"""
from __future__ import annotations

import functools
import os
import ssl
import tempfile
from pathlib import Path

import certifi
import httpx

_INTERMEDIATE_PEM = Path(__file__).with_name("certs") / "dsebd_intermediate.pem"


@functools.lru_cache(maxsize=1)
def dse_ssl_context() -> ssl.SSLContext:
    """Verifying SSL context = certifi roots + the pinned DSE intermediate."""
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.load_verify_locations(cafile=str(_INTERMEDIATE_PEM))
    return ctx


@functools.lru_cache(maxsize=1)
def dse_ca_bundle_path() -> str:
    """Path to a combined PEM (certifi roots + pinned DSE intermediate).

    For libraries that verify via OpenSSL/`requests` env vars instead of an
    httpx context (e.g. bdshare). Written once per process to the temp dir.
    """
    combined = Path(tempfile.gettempdir()) / "dsebd_ca_bundle.pem"
    combined.write_bytes(
        Path(certifi.where()).read_bytes() + b"\n" + _INTERMEDIATE_PEM.read_bytes()
    )
    return str(combined)


def use_dse_ca_for_requests() -> None:
    """Point `requests`/urllib at the combined DSE bundle so libraries like
    bdshare can verify dsebd.org's incomplete chain (same fix as
    dse_ssl_context, but via REQUESTS_CA_BUNDLE / SSL_CERT_FILE).

    Idempotent. setdefault so an operator-supplied bundle is never clobbered.
    """
    bundle = dse_ca_bundle_path()
    os.environ.setdefault("REQUESTS_CA_BUNDLE", bundle)
    os.environ.setdefault("SSL_CERT_FILE", bundle)


def dse_client(**kwargs: object) -> httpx.AsyncClient:
    """
    httpx.AsyncClient pre-configured for dsebd.org's broken chain.

    Sets the pinned-intermediate verify context and follow_redirects=True by
    default; callers still pass timeout / headers as before.
    """
    kwargs.setdefault("follow_redirects", True)
    kwargs.setdefault("verify", dse_ssl_context())
    return httpx.AsyncClient(**kwargs)  # type: ignore[arg-type]
