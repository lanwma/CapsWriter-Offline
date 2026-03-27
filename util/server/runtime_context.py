import os
import sys
import logging
from typing import Optional

from rich.console import Console

logger = logging.getLogger("server")


def get_inheritable_stdin_fileno() -> Optional[int]:
    """获取可传递给子进程的 stdin 句柄，无控制台时返回 None。"""
    try:
        stdin = sys.stdin
        if stdin is None or stdin.closed:
            return None
        return stdin.fileno()
    except (AttributeError, OSError, ValueError):
        logger.debug("当前运行环境没有可用的 stdin 句柄，子进程将以无控制台模式启动")
        return None


def attach_inherited_stdin(stdin_fileno: Optional[int]) -> bool:
    """在子进程中恢复继承的 stdin；无句柄时安全跳过。"""
    if stdin_fileno is None:
        return False

    try:
        sys.stdin = os.fdopen(os.dup(stdin_fileno))
        return True
    except OSError as exc:
        logger.warning(f"恢复继承 stdin 失败，将继续以无控制台模式运行: {exc}")
        return False


def has_interactive_stdin() -> bool:
    """当前进程是否存在可交互 stdin。"""
    try:
        stdin = sys.stdin
        return bool(stdin and not stdin.closed and stdin.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def has_console_output() -> bool:
    """当前进程是否存在可用控制台输出流。"""
    try:
        stdout = sys.stdout
        return bool(stdout and not stdout.closed)
    except (AttributeError, OSError, ValueError):
        return False


def create_server_console() -> Console:
    """创建兼容 windowed/console 两种模式的 Rich Console。"""
    if has_console_output():
        return Console(highlight=False)

    sink = open(os.devnull, "w", encoding="utf-8")
    return Console(file=sink, highlight=False)
