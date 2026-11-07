from __future__ import annotations

import os
import sys
from pathlib import Path


def _chown_tree(root: Path, uid: int, gid: int) -> None:
    os.chown(root, uid, gid)
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            os.chown(Path(dirpath) / name, uid, gid)


def main() -> None:
    data = Path(os.environ.get("DATA_DIR", "/data"))
    data.mkdir(parents=True, exist_ok=True)
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        uid = int(os.environ.get("PUID", "568"))
        gid = int(os.environ.get("PGID", "568"))
        if uid != 0:
            _chown_tree(data, uid, gid)
            os.setgid(gid)
            os.setuid(uid)
    argv = sys.argv[1:] or ["python", "-m", "app"]
    os.execvp(argv[0], argv)


if __name__ == "__main__":
    main()
