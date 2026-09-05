import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from kbquant.config import settings

LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(log_file: str | None = None) -> None:
    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    log_path = log_dir / (log_file or settings.log_file)
    # Use per-process log file on Windows to avoid RotatingFileHandler conflicts
    if os.name == "nt":
        pid = os.getpid()
        log_path = log_path.parent / f"{log_path.stem}_pid{pid}{log_path.suffix}"

    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)

    root = logging.getLogger()
    root.setLevel(level)

    # 控制台输出
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    root.addHandler(console)

    # 文件输出 + 轮转
    file_handler = RotatingFileHandler(
        str(log_path),
        maxBytes=settings.log_file_max_bytes,
        backupCount=settings.log_file_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # 抑制第三方库的噪音日志
    for name in ("elasticsearch", "elastic_transport", "httpx", "httpcore", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)
