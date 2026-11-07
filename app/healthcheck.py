from __future__ import annotations

import os
import ssl
import urllib.request
from pathlib import Path


def main() -> None:
    port = os.environ.get("PORT", "8787")
    data = Path(os.environ.get("DATA_DIR", "/data"))
    secure = (data / "tls" / "cert.pem").is_file() and (data / "tls" / "key.pem").is_file()
    scheme = "https" if secure else "http"
    context = ssl._create_unverified_context() if secure else None
    with urllib.request.urlopen(f"{scheme}://127.0.0.1:{port}/health", timeout=4, context=context) as response:
        if response.status != 200:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
