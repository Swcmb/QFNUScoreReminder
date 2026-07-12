"""CLI 入口测试：参数解析、模式互斥、--version、--user 过滤、--interval 边界。"""
from __future__ import annotations

import textwrap

import pytest
from click.testing import CliRunner

from qfnu_score import __version__
from qfnu_score.cli import main


@pytest.fixture
def runner():
    return CliRunner()


def test_version(runner):
    """A1: --version 输出版本号。"""
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_watch_and_once_mutex(runner):
    """--watch 与 --once 不能同时传。"""
    result = runner.invoke(main, ["--watch", "--once", "--config", "x.yaml"])
    assert result.exit_code == 1
    assert "不能同时" in result.output


def test_interval_without_watch(runner):
    """非 --watch 模式下传 --interval 报错。"""
    result = runner.invoke(main, ["--interval", "5", "--config", "x.yaml"])
    assert result.exit_code == 1
    assert "仅在 --watch" in result.output


def test_interval_below_one(runner):
    """--interval < 1 报错。"""
    result = runner.invoke(main, ["--watch", "--interval", "0", "--config", "x.yaml"])
    assert result.exit_code == 1
    assert ">= 1" in result.output


def test_config_missing(runner, tmp_path):
    """A2: config.yaml 缺失时报错并提示。"""
    nonexistent = tmp_path / "nonexistent.yaml"
    result = runner.invoke(main, ["--config", str(nonexistent)])
    assert result.exit_code == 1
    assert "不存在" in result.output
    assert "config.example.yaml" in result.output


def test_user_not_found(runner, tmp_path):
    """--user 传入不存在的 account 报错并列出可用。"""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            semester: "2024-2025-2"
            users:
              - account: "20240001"
                password: "p"
            """
        ),
        encoding="utf-8",
    )
    result = runner.invoke(main, ["--config", str(cfg), "--user", "99999999"])
    assert result.exit_code == 1
    assert "99999999" in result.output
    assert "20240001" in result.output  # 列出可用


def test_user_filter_run_once(runner, tmp_path, monkeypatch):
    """--user 20240001 仅运行该用户（mock jwxt）。"""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            semester: "2024-2025-2"
            users:
              - account: "20240001"
                password: "p"
              - account: "20240002"
                password: "p"
            """
        ),
        encoding="utf-8",
    )

    called_accounts: list[str] = []

    def fake_login(account, password):
        called_accounts.append(account)
        return ("session", {})

    monkeypatch.setattr("qfnu_score.monitor.jwxt.login", fake_login)
    monkeypatch.setattr(
        "qfnu_score.monitor.jwxt.fetch_scores", lambda s, c, sem: []
    )
    monkeypatch.setattr("qfnu_score.monitor.store.exists", lambda a: True)
    monkeypatch.setattr("qfnu_score.monitor.store.load", lambda a: [])
    monkeypatch.setattr("qfnu_score.monitor.store.diff", lambda c, l: [])
    monkeypatch.setattr("qfnu_score.monitor.notify", lambda *a, **kw: None)

    result = runner.invoke(main, ["--config", str(cfg), "--user", "20240001"])
    assert result.exit_code == 0
    assert called_accounts == ["20240001"]


def test_user_dedup(runner, tmp_path, monkeypatch):
    """--user 重复传同一 account 自动去重，只运行一次。"""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            semester: "2024-2025-2"
            users:
              - account: "20240001"
                password: "p"
            """
        ),
        encoding="utf-8",
    )

    called_accounts: list[str] = []

    def fake_login(account, password):
        called_accounts.append(account)
        return ("session", {})

    monkeypatch.setattr("qfnu_score.monitor.jwxt.login", fake_login)
    monkeypatch.setattr(
        "qfnu_score.monitor.jwxt.fetch_scores", lambda s, c, sem: []
    )
    monkeypatch.setattr("qfnu_score.monitor.store.exists", lambda a: True)
    monkeypatch.setattr("qfnu_score.monitor.store.load", lambda a: [])
    monkeypatch.setattr("qfnu_score.monitor.store.diff", lambda c, l: [])
    monkeypatch.setattr("qfnu_score.monitor.notify", lambda *a, **kw: None)

    result = runner.invoke(
        main, ["--config", str(cfg), "--user", "20240001", "--user", "20240001"]
    )
    assert result.exit_code == 0
    assert called_accounts == ["20240001"]  # 只调用一次
