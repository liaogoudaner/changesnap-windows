"""日志配置工具。"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from utils.file_utils import get_logs_dir


_loggers: dict[str, logging.Logger] = {}


def setup_logger(
    name: str = "changesnap",
    level: int = logging.INFO,
    log_file: Optional[Path] = None,
) -> logging.Logger:
    """配置并返回 logger。

    Args:
        name: logger 名称
        level: 日志级别
        log_file: 日志文件路径，默认自动按日生成

    Returns:
        配置好的 logger
    """
    if name in _loggers:
        return _loggers[name]

    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 清除已有 handler
    logger.handlers.clear()

    # 格式
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    # 控制台 handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件 handler
    if log_file is None:
        log_dir = get_logs_dir()
        today = datetime.now().strftime('%Y%m%d')
        log_file = log_dir / f'changesnap_{today}.log'

    file_handler = logging.FileHandler(str(log_file), encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    _loggers[name] = logger
    return logger


def get_logger(name: str = "changesnap") -> logging.Logger:
    """获取已配置的 logger。"""
    if name in _loggers:
        return _loggers[name]
    return setup_logger(name)
