"""所有运行数据都在固定目录，避免随工作目录生成散落文件。"""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DataPaths:
    root: Path

    @classmethod
    def create(cls, directory=None):
        default = Path(__file__).resolve().parent.parent / "data"
        root = Path(directory or os.environ.get("ACTOR_DATA_DIR") or default).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in ["cache", "logs", "exports"]:
            (root / name).mkdir(exist_ok=True, mode=0o700)
        return cls(root)

    @property
    def cache(self):
        return self.root / "cache"

    @property
    def database(self):
        return self.root / "state.sqlite"

    def export(self, value):
        path = Path(value)
        if path.is_absolute():
            return path
        destination = (self.root / "exports" / path).resolve()
        if not destination.is_relative_to(self.root / "exports"):
            raise ValueError("相对导出路径必须在 data/exports 内")
        return destination
