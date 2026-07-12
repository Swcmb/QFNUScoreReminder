"""配置加载与校验。

从 YAML 文件加载配置并按 §4.2 规则校验，返回强类型对象。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

import yaml

# account 白名单：防路径注入
ACCOUNT_RE = re.compile(r"^[A-Za-z0-9_-]{4,32}$")
# 学期格式：YYYY-YYYY-X
SEMESTER_RE = re.compile(r"^\d{4}-\d{4}-[1-2]$")


class ConfigError(Exception):
    """配置错误，对应退出码 1。"""


class PasswordMissingError(Exception):
    """密码对应的环境变量未设置。"""


@dataclass
class DingtalkWebhook:
    token: str
    secret: str


@dataclass
class FeishuWebhook:
    webhook_url: str
    secret: str


@dataclass
class DefaultsConfig:
    dingtalk: list[DingtalkWebhook] = field(default_factory=list)
    feishu: list[FeishuWebhook] = field(default_factory=list)


@dataclass
class UserConfig:
    account: str
    password: str  # 未经 ENV: 解析的原始值
    semester: Optional[str] = None
    dingtalk: list[DingtalkWebhook] = field(default_factory=list)
    feishu: list[FeishuWebhook] = field(default_factory=list)

    def resolve_password(self) -> str:
        """按 §4.1 规则解析密码：ENV: 前缀从环境变量取，否则视为明文。"""
        if self.password.startswith("ENV:"):
            var_name = self.password[4:]
            actual = os.environ.get(var_name)
            if actual is None:
                raise PasswordMissingError(self.account, var_name)
            return actual
        return self.password

    def effective_semester(self, default_semester: str) -> str:
        return self.semester if self.semester else default_semester


@dataclass
class AppConfig:
    interval_minutes: int
    semester: str
    defaults: DefaultsConfig
    users: list[UserConfig]


def _parse_dingtalk_list(items: list, where: str) -> list[DingtalkWebhook]:
    out: list[DingtalkWebhook] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ConfigError(f"{where}[{i}] 必须是字典")
        token = item.get("token")
        secret = item.get("secret")
        if not token or not secret:
            raise ConfigError(f"{where}[{i}] 缺少 token 或 secret")
        out.append(DingtalkWebhook(token=token, secret=secret))
    return out


def _parse_feishu_list(items: list, where: str) -> list[FeishuWebhook]:
    out: list[FeishuWebhook] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ConfigError(f"{where}[{i}] 必须是字典")
        url = item.get("webhook_url")
        secret = item.get("secret")
        if not url or not secret:
            raise ConfigError(f"{where}[{i}] 缺少 webhook_url 或 secret")
        out.append(FeishuWebhook(webhook_url=url, secret=secret))
    return out


def load_config(path: str) -> AppConfig:
    """加载并校验配置文件，返回 AppConfig。

    Args:
        path: 配置文件路径

    Raises:
        ConfigError: 文件缺失/格式错误/校验失败
    """
    if not os.path.exists(path):
        raise ConfigError(
            f"配置文件 {path} 不存在，请复制 config.example.yaml 为 {path} 后编辑使用"
        )

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"配置文件 {path} YAML 格式错误: {e}")

    if not isinstance(raw, dict):
        raise ConfigError(f"配置文件 {path} 顶层必须是字典")

    # interval_minutes（可选；--watch 模式下缺失才报错，由 cli 判断）
    interval_minutes = raw.get("interval_minutes", 0)
    if not isinstance(interval_minutes, int) or interval_minutes < 0:
        raise ConfigError("interval_minutes 必须是非负整数")
    if interval_minutes == 0:
        interval_minutes = 0  # 标记为未配置，由 cli 校验
    else:
        if interval_minutes < 1:
            raise ConfigError("interval_minutes 必须 >= 1")

    semester = raw.get("semester", "2024-2025-2")
    if not SEMESTER_RE.match(semester):
        raise ConfigError(f"semester 格式错误: {semester}（应为 YYYY-YYYY-X）")

    # defaults
    defaults_raw = raw.get("defaults") or {}
    if not isinstance(defaults_raw, dict):
        raise ConfigError("defaults 必须是字典")
    defaults = DefaultsConfig(
        dingtalk=_parse_dingtalk_list(defaults_raw.get("dingtalk") or [], "defaults.dingtalk"),
        feishu=_parse_feishu_list(defaults_raw.get("feishu") or [], "defaults.feishu"),
    )

    # users
    users_raw = raw.get("users") or []
    if not users_raw:
        raise ConfigError("users 至少 1 个")
    if not isinstance(users_raw, list):
        raise ConfigError("users 必须是列表")

    users: list[UserConfig] = []
    seen_accounts: set[str] = set()
    for i, u_raw in enumerate(users_raw):
        if not isinstance(u_raw, dict):
            raise ConfigError(f"users[{i}] 必须是字典")
        account = u_raw.get("account")
        password = u_raw.get("password")
        if not account or not password:
            raise ConfigError(f"users[{i}] 缺少 account 或 password")
        if not ACCOUNT_RE.match(account):
            raise ConfigError(
                f"users[{i}].account '{account}' 不匹配 {ACCOUNT_RE.pattern}"
            )
        if account in seen_accounts:
            raise ConfigError(f"users[{i}].account '{account}' 重复")
        seen_accounts.add(account)

        u_semester = u_raw.get("semester")
        if u_semester is not None and not SEMESTER_RE.match(u_semester):
            raise ConfigError(
                f"users[{i}].semester 格式错误: {u_semester}"
            )

        users.append(
            UserConfig(
                account=account,
                password=password,
                semester=u_semester,
                dingtalk=_parse_dingtalk_list(u_raw.get("dingtalk") or [], f"users[{i}].dingtalk"),
                feishu=_parse_feishu_list(u_raw.get("feishu") or [], f"users[{i}].feishu"),
            )
        )

    return AppConfig(
        interval_minutes=interval_minutes,
        semester=semester,
        defaults=defaults,
        users=users,
    )
