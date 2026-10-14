"""启动单进程管理服务。"""

import argparse
import logging
from logging.handlers import RotatingFileHandler

import uvicorn

from .paths import DataPaths


def main():
    parser = argparse.ArgumentParser(description="Actor Scraper Web")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8891)
    parser.add_argument("--data-dir")
    args = parser.parse_args()
    paths = DataPaths.create(args.data_dir)
    logger = logging.getLogger("actor_scraper")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(paths.root / "logs" / "app.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    from .webapp import create_app

    app = create_app(paths.root)
    print(f"数据目录：{paths.root}；管理密码：ACTOR_ADMIN_PASSWORD 或 {paths.root / 'admin-password'}")
    uvicorn.run(app, host=args.host, port=args.port, workers=1)


if __name__ == "__main__":
    main()
