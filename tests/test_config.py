"""配置加载与校验规则测试。"""
from __future__ import annotations

import os
import textwrap

import pytest

from qfnu_score.config import (
    ACCOUNT_RE,
    ConfigError,
    PasswordMissingError,
    SEMESTER_RE,
    load_config,
)


def _write_config(tmp_path, content: str) -> str:
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return str(p)


def test_missing_file(tmp_path):
    """A2: 配置文件缺失时报错。"""
    with pytest.raises(ConfigError, match="不存在"):
        load_config(str(tmp_path / "nonexistent.yaml"))


def test_invalid_yaml(tmp_path):
    """格式错误时报错。"""
    p = tmp_path / "config.yaml"
    p.write_text("users: [\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML"):
        load_config(str(p))


def test_empty_users(tmp_path):
    """users 为空时报错。"""
    path = _write_config(
        tmp_path,
        """
        semester: "2024-2025-2"
        users: []
        """,
    )
    with pytest.raises(ConfigError, match="至少 1 个"):
        load_config(path)


def test_invalid_semester(tmp_path):
    """semester 格式错误时报错。"""
    path = _write_config(
        tmp_path,
        """
        semester: "bad"
        users:
          - account: "20240001"
            password: "p"
        """,
    )
    with pytest.raises(ConfigError, match="semester"):
        load_config(path)


def test_invalid_account(tmp_path):
    """account 不匹配白名单时报错（防路径注入）。"""
    path = _write_config(
        tmp_path,
        """
        semester: "2024-2025-2"
        users:
          - account: "../../etc/passwd"
            password: "p"
        """,
    )
    with pytest.raises(ConfigError, match="account"):
        load_config(path)


def test_duplicate_account(tmp_path):
    """同一 account 重复时报错。"""
    path = _write_config(
        tmp_path,
        """
        semester: "2024-2025-2"
        users:
          - account: "20240001"
            password: "p"
          - account: "20240001"
            password: "p2"
        """,
    )
    with pytest.raises(ConfigError, match="重复"):
        load_config(path)


def test_dingtalk_missing_field(tmp_path):
    """钉钉配置缺字段时报错。"""
    path = _write_config(
        tmp_path,
        """
        semester: "2024-2025-2"
        defaults:
          dingtalk:
            - token: "abc"
        users:
          - account: "20240001"
            password: "p"
        """,
    )
    with pytest.raises(ConfigError, match="token 或 secret"):
        load_config(path)


def test_env_password_resolve(tmp_path, monkeypatch):
    """ENV: 前缀从环境变量取。"""
    path = _write_config(
        tmp_path,
        """
        semester: "2024-2025-2"
        users:
          - account: "20240001"
            password: "ENV:TEST_PW_20240001"
        """,
    )
    monkeypatch.setenv("TEST_PW_20240001", "real_secret")
    cfg = load_config(path)
    assert cfg.users[0].resolve_password() == "real_secret"


def test_env_password_missing(tmp_path, monkeypatch):
    """ENV: 环境变量不存在时报 PasswordMissingError。"""
    path = _write_config(
        tmp_path,
        """
        semester: "2024-2025-2"
        users:
          - account: "20240001"
            password: "ENV:NOT_SET_VAR"
        """,
    )
    monkeypatch.delenv("NOT_SET_VAR", raising=False)
    cfg = load_config(path)
    with pytest.raises(PasswordMissingError):
        cfg.users[0].resolve_password()


def test_plaintext_password(tmp_path):
    """明文密码直接返回。"""
    path = _write_config(
        tmp_path,
        """
        semester: "2024-2025-2"
        users:
          - account: "20240001"
            password: "plain_pw"
        """,
    )
    cfg = load_config(path)
    assert cfg.users[0].resolve_password() == "plain_pw"


def test_valid_full_config(tmp_path):
    """完整合法配置加载成功。"""
    path = _write_config(
        tmp_path,
        """
        interval_minutes: 10
        semester: "2024-2025-2"
        defaults:
          dingtalk:
            - token: "gt"
              secret: "gs"
          feishu:
            - webhook_url: "https://example.com/hook"
              secret: "gfs"
        users:
          - account: "20240001"
            password: "p1"
            semester: "2025-2026-1"
            dingtalk:
              - token: "ut"
                secret: "us"
          - account: "20240002"
            password: "ENV:PW2"
        """,
    )
    cfg = load_config(path)
    assert cfg.interval_minutes == 10
    assert cfg.semester == "2024-2025-2"
    assert len(cfg.users) == 2
    assert cfg.users[0].effective_semester(cfg.semester) == "2025-2026-1"
    assert cfg.users[1].effective_semester(cfg.semester) == "2024-2025-2"
    assert len(cfg.defaults.dingtalk) == 1
    assert len(cfg.defaults.feishu) == 1
    # 用户级覆盖
    assert len(cfg.users[0].dingtalk) == 1
    # 用户级为空回退到默认（resolve 时才发生）
    assert cfg.users[1].dingtalk == []


def test_account_regex():
    """account 正则覆盖。"""
    assert ACCOUNT_RE.match("20240001")
    assert ACCOUNT_RE.match("user_2024-x")
    assert not ACCOUNT_RE.match("../etc")
    assert not ACCOUNT_RE.match("a")  # 太短
    assert not ACCOUNT_RE.match("a" * 33)  # 太长


def test_semester_regex():
    assert SEMESTER_RE.match("2024-2025-2")
    assert SEMESTER_RE.match("2025-2026-1")
    assert not SEMESTER_RE.match("2024-2025-3")  # 学期只能 1 或 2
    assert not SEMESTER_RE.match("bad")
