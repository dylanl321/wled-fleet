from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

from app.config import Settings


def main() -> None:
    names = sys.argv[1:]
    if not names:
        raise SystemExit("Usage: python -m app.mkcert <ip-or-hostname> [more names]")
    settings = Settings.from_env()
    folder = settings.data_dir / "tls"
    folder.mkdir(parents=True, exist_ok=True)
    cert = folder / "cert.pem"
    key = folder / "key.pem"
    alt = []
    for name in names:
        try:
            socket.inet_aton(name)
            alt.append(f"IP:{name}")
        except OSError:
            alt.append(f"DNS:{name}")
    san = ",".join(alt)
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-sha256",
            "-days",
            "825",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-subj",
            "/CN=wled-fleet",
            "-addext",
            f"subjectAltName={san}",
        ],
        check=True,
    )
    print(f"Wrote {cert}")
    print("Restart the container, then open https://<that-name>:8787")


if __name__ == "__main__":
    main()
