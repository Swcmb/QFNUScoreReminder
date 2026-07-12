"""store 模块测试：原子写入、初始化分支、diff 计算。"""
from __future__ import annotations

import json
import os

import pytest

from qfnu_score import store


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """重定向 DATA_DIR 到临时目录。"""
    monkeypatch.setattr(store, "DATA_DIR", str(tmp_path / "data"))
    return store.DATA_DIR


def test_exists_false_for_new(data_dir):
    assert not store.exists("20240001")


def test_save_atomic_and_load(data_dir):
    scores = [["数学", "90"], ["英语", "85"]]
    store.save_atomic("20240001", scores)
    assert store.exists("20240001")
    loaded = store.load("20240001")
    assert loaded == scores


def test_load_returns_empty_for_missing(data_dir):
    assert store.load("99999999") == []


def test_save_atomic_no_tmp_left(data_dir):
    store.save_atomic("20240001", [["数学", "90"]])
    files = os.listdir(store.DATA_DIR)
    # 不应留下临时文件
    assert all(not f.startswith(".") for f in files)
    assert "20240001.json" in files


def test_save_atomic_overwrites(data_dir):
    store.save_atomic("20240001", [["数学", "90"]])
    store.save_atomic("20240001", [["数学", "90"], ["英语", "85"]])
    loaded = store.load("20240001")
    assert len(loaded) == 2


def test_diff_empty():
    """无新成绩。"""
    current = [["数学", "90"]]
    last = [["数学", "90"]]
    assert store.diff(current, last) == []


def test_diff_new_added():
    """新增一条。"""
    current = [["数学", "90"], ["英语", "85"]]
    last = [["数学", "90"]]
    assert store.diff(current, last) == [["英语", "85"]]


def test_diff_order_irrelevant():
    """diff 与顺序无关，按 tuple 比较。"""
    current = [["英语", "85"], ["数学", "90"]]
    last = [["数学", "90"]]
    assert store.diff(current, last) == [["英语", "85"]]


def test_load_corrupt_file(data_dir):
    """文件存在但内容损坏时抛 JSONDecodeError。"""
    os.makedirs(store.DATA_DIR, exist_ok=True)
    with open(os.path.join(store.DATA_DIR, "20240001.json"), "w") as f:
        f.write("{not valid json")
    with pytest.raises(json.JSONDecodeError):
        store.load("20240001")


def test_load_empty_file_returns_empty(data_dir):
    """空文件视为空列表。"""
    os.makedirs(store.DATA_DIR, exist_ok=True)
    with open(os.path.join(store.DATA_DIR, "20240001.json"), "w") as f:
        f.write("")
    assert store.load("20240001") == []
