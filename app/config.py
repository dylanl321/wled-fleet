from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.util import env_bool

APP_VERSION = "1.0.0"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    port: int = 8787
    admin_password: str = ""
    scan_subnets: str = ""
    extra_hosts: str = ""
    backup_interval: int = 86400
    backup_watch_interval: int = 900
    backup_retention: int = 30
    discovery_interval: int = 60
    presence_interval: int = 20
    scan_concurrency: int = 48
    scan_timeout: float = 1.2
    scheduler_enabled: bool = True
    wled_github_repo: str = "wled/WLED"
    quinled_github_repo: str = "intermittech/QuinLED-Firmware"
    ota_timeout: float = 180.0
    log_level: str = "INFO"
    app_version: str = APP_VERSION
    player_url: str = ""
    player_kind: str = "xschedule"
    player_playlist: str = ""
    xlights_show: str = ""

    @staticmethod
    def from_env() -> Settings:
        data_dir = Path(os.environ.get("DATA_DIR", "./data")).expanduser()
        return Settings(
            data_dir=data_dir,
            port=int(os.environ.get("PORT", "8787")),
            admin_password=os.environ.get("ADMIN_PASSWORD", ""),
            scan_subnets=os.environ.get("SCAN_SUBNETS", ""),
            extra_hosts=os.environ.get("EXTRA_HOSTS", ""),
            backup_interval=int(os.environ.get("BACKUP_INTERVAL", "86400")),
            backup_watch_interval=int(os.environ.get("BACKUP_WATCH_INTERVAL", "900")),
            backup_retention=int(os.environ.get("BACKUP_RETENTION", "30")),
            discovery_interval=int(os.environ.get("DISCOVERY_INTERVAL", "60")),
            presence_interval=int(os.environ.get("PRESENCE_INTERVAL", "20")),
            scan_concurrency=int(os.environ.get("SCAN_CONCURRENCY", "48")),
            scan_timeout=float(os.environ.get("SCAN_TIMEOUT", "1.2")),
            scheduler_enabled=env_bool(os.environ.get("SCHEDULER_ENABLED"), True),
            wled_github_repo=os.environ.get("WLED_GITHUB_REPO", "wled/WLED").strip(),
            quinled_github_repo=os.environ.get("QUINLED_GITHUB_REPO", "intermittech/QuinLED-Firmware").strip(),
            ota_timeout=float(os.environ.get("OTA_TIMEOUT", "180")),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            player_url=os.environ.get("MAPPER_PLAYER_URL", "").strip(),
            player_kind=os.environ.get("MAPPER_PLAYER_KIND", "xschedule").strip() or "xschedule",
            player_playlist=os.environ.get("MAPPER_PLAYLIST", "").strip(),
            xlights_show=os.environ.get("MAPPER_XLIGHTS_SHOW", "").strip(),
        )

    @property
    def tls_cert(self) -> Path:
        return self.data_dir / "tls" / "cert.pem"

    @property
    def tls_key(self) -> Path:
        return self.data_dir / "tls" / "key.pem"

    def tls_enabled(self) -> bool:
        return self.tls_cert.is_file() and self.tls_key.is_file()
