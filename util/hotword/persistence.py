from __future__ import annotations

from pathlib import Path
from typing import Iterable

from config_client import BASE_DIR

from . import logger


def append_lines_to_hotword_file(filename: str, lines: Iterable[str]) -> list[Path]:
    """将新增内容追加到运行文件，开发态下同步回源码模板。"""
    normalized_lines = [line.strip() for line in lines if line and line.strip()]
    if not normalized_lines:
        return []

    written_files: list[Path] = []
    for target in _iter_persist_targets(filename):
        _append_lines(target, normalized_lines)
        written_files.append(target)
    return written_files


def _iter_persist_targets(filename: str) -> list[Path]:
    runtime_file = (Path(BASE_DIR) / filename).resolve()
    targets = [runtime_file]

    source_file = _resolve_source_template_file(filename, runtime_file)
    if source_file is not None:
        targets.append(source_file)

    return targets


def _resolve_source_template_file(filename: str, runtime_file: Path) -> Path | None:
    runtime_root = runtime_file.parent
    dist_dir = runtime_root.parent
    source_root = dist_dir.parent

    if dist_dir.name.lower() != 'dist':
        return None

    project_markers = ('build.spec', 'core_client.py', 'config_client.py')
    if not all((source_root / marker).exists() for marker in project_markers):
        return None

    source_file = (source_root / filename).resolve()
    if source_file == runtime_file:
        return None

    logger.debug(f"检测到开发态发行目录，热词将同步回源码模板: {source_file}")
    return source_file


def _append_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()

    content = path.read_text(encoding='utf-8')
    needs_newline = bool(content and not content.endswith('\n'))

    with path.open('a', encoding='utf-8') as file:
        if needs_newline:
            file.write('\n')
        for line in lines:
            file.write(f"{line}\n")

