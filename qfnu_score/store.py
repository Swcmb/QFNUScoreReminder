"""按学号持久化成绩状态，原子写入。

文件路径：data/<account>.json
结构：[[subject_name, score], ...]
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Optional

log = logging.getLogger(__name__)

# 成绩状态目录（相对工作目录）
DATA_DIR = "data"


def _ensure_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _path_for(account: str) -> str:
    """account 已在 config 层做过白名单校验，这里仅拼路径。"""
    return os.path.join(DATA_DIR, f"{account}.json")


def exists(account: str) -> bool:
    return os.path.exists(_path_for(account))


def load(account: str) -> list[list[str]]:
    """读取某用户的上次成绩。文件不存在时返回空列表（视为初始化）。

    Raises:
        json.JSONDecodeError: 文件存在但内容损坏（上层捕获计入 failures）
    """
    path = _path_for(account)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = f.read()
    if not data.strip():
        return []
    return json.loads(data)


def save_atomic(account: str, scores: list[list[str]]) -> None:
    """原子写入：先写临时文件再 os.replace。

    避免进程中断产生半截 JSON。
    """
    _ensure_dir()
    path = _path_for(account)
    dir_ = os.path.dirname(path) or "."
    # 在同一目录创建临时文件，确保与目标文件在同一文件系统
    fd, tmp_path = tempfile.mkstemp(dir=dir_, prefix=f".{account}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(scores, f, ensure_ascii=False, indent=4)
        os.replace(tmp_path, path)
    except Exception:
        # 清理临时文件
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


def diff(current: list[list[str]], last: list[list[str]]) -> list[list[str]]:
    """返回 current 中存在但 last 中不存在的成绩条目。"""
    last_set = {tuple(s) for s in last}
    return [s for s in current if tuple(s) not in last_set]
