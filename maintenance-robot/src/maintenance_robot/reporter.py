from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List


@dataclass
class DownloadUpdate:
    file: Path
    identifier: str
    previous: str
    updated: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "file": str(self.file),
            "identifier": self.identifier,
            "previous": self.previous,
            "updated": self.updated,
        }


@dataclass
class MaintenanceReport:
    downloads: List[DownloadUpdate] = field(default_factory=list)

    def add_download_update(self, update: DownloadUpdate) -> None:
        self.downloads.append(update)

    def to_dict(self) -> Dict[str, List[Dict[str, object]]]:
        return {
            "downloads": [item.to_dict() for item in self.downloads],
        }
