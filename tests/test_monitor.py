"""monitor 模块测试：通知回退、退出码判定、--user 过滤、--dry-run。"""
from __future__ import annotations

import pytest

from qfnu_score.config import (
    AppConfig,
    DefaultsConfig,
    DingtalkWebhook,
    FeishuWebhook,
    UserConfig,
)
from qfnu_score.jwxt import LoginError, NetworkError
from qfnu_score import monitor


@pytest.fixture
def defaults():
    return DefaultsConfig(
        dingtalk=[DingtalkWebhook(token="gt", secret="gs")],
        feishu=[FeishuWebhook(webhook_url="https://example.com/h", secret="gfs")],
    )


@pytest.fixture
def user_with_own_dingtalk():
    return UserConfig(
        account="20240001",
        password="p",
        dingtalk=[DingtalkWebhook(token="ut", secret="us")],
        feishu=[],  # 用户级为空
    )


@pytest.fixture
def user_with_no_notifiers():
    return UserConfig(account="20240002", password="p")


def test_resolve_notifiers_user_overrides_default(defaults, user_with_own_dingtalk):
    """A5: 用户级钉钉优先于全局默认。"""
    dt, fs = monitor.resolve_notifiers(user_with_own_dingtalk, defaults)
    assert len(dt) == 1
    assert dt[0].token == "ut"  # 用户级
    # 用户级飞书为空 → 回退默认
    assert len(fs) == 1
    assert fs[0].secret == "gfs"


def test_resolve_notifiers_fallback_to_defaults(defaults, user_with_no_notifiers):
    """用户未配则回退默认。"""
    dt, fs = monitor.resolve_notifiers(user_with_no_notifiers, defaults)
    assert len(dt) == 1
    assert dt[0].token == "gt"
    assert len(fs) == 1


def test_resolve_notifiers_empty_defaults():
    """defaults 为空且用户未配 → 两渠道都空。"""
    empty_defaults = DefaultsConfig()
    user = UserConfig(account="20240001", password="p")
    dt, fs = monitor.resolve_notifiers(user, empty_defaults)
    assert dt == []
    assert fs == []


def test_determine_exit_code_all_success():
    """全部成功 → 0。"""
    result = monitor.RunResult(success_count=3, failures=[])
    assert monitor.determine_exit_code(result, 3, config_error=False) == 0


def test_determine_exit_code_config_error():
    """配置错误 → 1（无视其它）。"""
    result = monitor.RunResult(success_count=3, failures=[])
    assert monitor.determine_exit_code(result, 3, config_error=True) == 1


def test_determine_exit_code_all_network_error():
    """0 成功 + 全部 NetworkError → 2。"""
    result = monitor.RunResult(
        success_count=0,
        failures=[
            ("a1", NetworkError("net1")),
            ("a2", NetworkError("net2")),
        ],
    )
    assert monitor.determine_exit_code(result, 2, config_error=False) == 2


def test_determine_exit_code_partial_failure_with_login_error():
    """A10: 部分成功（含 LoginError）→ 3。"""
    result = monitor.RunResult(
        success_count=2,
        failures=[("a3", LoginError("wrong password"))],
    )
    assert monitor.determine_exit_code(result, 3, config_error=False) == 3


def test_determine_exit_code_all_login_error():
    """0 成功 + 全部 LoginError → 3（非系统级不可达）。"""
    result = monitor.RunResult(
        success_count=0,
        failures=[("a1", LoginError("e1")), ("a2", LoginError("e2"))],
    )
    assert monitor.determine_exit_code(result, 2, config_error=False) == 3


def test_determine_exit_code_mixed_network_and_login():
    """0 成功 + 混合 NetworkError/LoginError → 3。"""
    result = monitor.RunResult(
        success_count=0,
        failures=[("a1", NetworkError("net")), ("a2", LoginError("login"))],
    )
    assert monitor.determine_exit_code(result, 2, config_error=False) == 3


def test_determine_exit_code_partial_success_partial_network():
    """1 成功 + 1 NetworkError 失败 → 3（不是 2，因为有成功）。"""
    result = monitor.RunResult(
        success_count=1,
        failures=[("a2", NetworkError("net"))],
    )
    assert monitor.determine_exit_code(result, 2, config_error=False) == 3


def test_split_body_short():
    """短正文不拆分。"""
    parts = monitor._split_body("short text")
    assert parts == ["short text"]


def test_split_body_long():
    """长正文按阈值拆分。"""
    # 暂时改小阈值便于测试
    import qfnu_score.monitor as m
    original = m.SPLIT_THRESHOLD
    m.SPLIT_THRESHOLD = 10
    try:
        body = "a" * 25
        parts = m._split_body(body)
        assert len(parts) == 3
        assert parts[0] == "a" * 10
        assert parts[1] == "a" * 10
        assert parts[2] == "a" * 5
    finally:
        m.SPLIT_THRESHOLD = original


def test_build_body():
    body = monitor._build_body("20240001", "测试消息")
    assert body == "学号: 20240001\n测试消息"


def test_notify_message_and_new_scores_mutex(defaults, user_with_no_notifiers):
    """message 与 new_scores 互斥。"""
    with pytest.raises(ValueError, match="互斥"):
        monitor.notify(
            user_with_no_notifiers,
            defaults,
            message="msg",
            new_scores=[["数学", "90"]],
        )


def test_notify_neither_message_nor_scores(defaults, user_with_no_notifiers):
    """都不传报错。"""
    with pytest.raises(ValueError, match="必须传其一"):
        monitor.notify(user_with_no_notifiers, defaults)


def test_run_once_dry_run_no_side_effects(defaults, monkeypatch):
    """A9: --dry-run 模式下 store 与 notify 均不被调用。"""
    user = UserConfig(account="20240001", password="p")
    cfg = AppConfig(
        interval_minutes=5,
        semester="2024-2025-2",
        defaults=defaults,
        users=[user],
    )

    # mock jwxt 与 store
    called = {"save": 0, "notify": 0}

    def fake_save(*args, **kwargs):
        called["save"] += 1

    def fake_notify(*args, **kwargs):
        called["notify"] += 1

    monkeypatch.setattr(monitor.jwxt, "login", lambda a, p: ("session", {}))
    monkeypatch.setattr(
        monitor.jwxt, "fetch_scores", lambda s, c, sem: [["数学", "90"]]
    )
    monkeypatch.setattr(monitor.store, "exists", lambda a: False)
    monkeypatch.setattr(monitor.store, "save_atomic", fake_save)
    monkeypatch.setattr(monitor.store, "load", lambda a: [])
    monkeypatch.setattr(monitor.store, "diff", lambda c, l: c)
    monkeypatch.setattr(monitor, "notify", fake_notify)

    result = monitor.run_once(cfg, None, dry_run=True)
    assert called["save"] == 0
    assert called["notify"] == 0
    assert result.success_count == 1


def test_run_once_partial_failure_exit_code(defaults, monkeypatch):
    """A10: 某用户失败但其他成功，退出码 3。"""
    u1 = UserConfig(account="20240001", password="p")
    u2 = UserConfig(account="20240002", password="ENV:MISSING")
    cfg = AppConfig(
        interval_minutes=5,
        semester="2024-2025-2",
        defaults=defaults,
        users=[u1, u2],
    )

    def fake_login(account, password):
        if account == "20240002":
            raise LoginError("wrong password")
        return ("session", {})

    monkeypatch.setattr(monitor.jwxt, "login", fake_login)
    monkeypatch.setattr(
        monitor.jwxt, "fetch_scores", lambda s, c, sem: []
    )
    monkeypatch.setattr(monitor.store, "exists", lambda a: True)
    monkeypatch.setattr(monitor.store, "load", lambda a: [])
    monkeypatch.setattr(monitor.store, "diff", lambda c, l: [])
    monkeypatch.setattr(monitor, "notify", lambda *a, **kw: None)

    result = monitor.run_once(cfg, None, dry_run=False)
    code = monitor.determine_exit_code(result, 2, config_error=False)
    assert code == 3
    assert result.success_count == 1
    assert len(result.failures) == 1
