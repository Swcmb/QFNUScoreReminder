"""编排：遍历用户、对比成绩、触发通知。

包含一次性模式与守护模式的编排逻辑、退出码判定、通知回退。
"""
from __future__ import annotations

import logging
import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from qfnu_score import dingtalk, feishu, jwxt, store
from qfnu_score.config import (
    AppConfig,
    DefaultsConfig,
    PasswordMissingError,
    UserConfig,
)
from qfnu_score.jwxt import (
    CaptchaError,
    JwxtError,
    LoginError,
    NetworkError,
)

log = logging.getLogger(__name__)

# 通知标题
TITLE = "成绩监控通知"

# 超长拆分阈值（钉钉更小，取最小公倍数）
SPLIT_THRESHOLD = 18000


@dataclass
class RunResult:
    """单次执行结果。"""
    success_count: int = 0
    failures: list[tuple[str, Exception]] = field(default_factory=list)
    new_scores_count: int = 0


def resolve_notifiers(
    user: UserConfig, defaults: DefaultsConfig
) -> tuple[list, list]:
    """返回 (dingtalk_list, feishu_list)，用户级优先回退默认。"""
    dt = user.dingtalk if user.dingtalk else defaults.dingtalk
    fs = user.feishu if user.feishu else defaults.feishu
    return dt, fs


def _build_body(account: str, body: str) -> str:
    """构造完整正文：学号前缀 + 正文。"""
    return f"学号: {account}\n{body}"


def _split_body(body: str) -> list[str]:
    """超长正文拆分。"""
    if len(body) <= SPLIT_THRESHOLD:
        return [body]
    parts: list[str] = []
    i = 0
    while i < len(body):
        parts.append(body[i : i + SPLIT_THRESHOLD])
        i += SPLIT_THRESHOLD
    return parts


def notify(
    user: UserConfig,
    defaults: DefaultsConfig,
    *,
    message: Optional[str] = None,
    new_scores: Optional[list[list[str]]] = None,
) -> None:
    """发送通知。

    Args:
        user: 用户配置
        defaults: 全局默认配置
        message: 直接正文（初始化/出错通知）
        new_scores: 新成绩列表（新成绩通知）

    Raises:
        ValueError: message 与 new_scores 同时传或都不传
    """
    if message is not None and new_scores is not None:
        raise ValueError("message 与 new_scores 互斥")
    if message is None and new_scores is None:
        raise ValueError("message 与 new_scores 必须传其一")

    if new_scores is not None:
        lines = ["发现新成绩！"]
        for name, score in new_scores:
            lines.append(f"科目: {name}")
            lines.append(f"成绩: {score}")
        body = _build_body(user.account, "\n".join(lines))
    else:
        body = _build_body(user.account, message or "")

    dt_list, fs_list = resolve_notifiers(user, defaults)
    parts = _split_body(body)

    total = len(parts)
    for idx, part in enumerate(parts, 1):
        title = TITLE if total == 1 else f"{TITLE} ({idx}/{total})"
        # 钉钉逐个发送
        for wh in dt_list:
            try:
                dingtalk.send(wh.token, wh.secret, title, part)
            except Exception as e:
                log.error(f"钉钉发送失败 token=***: {e}")
        # 飞书逐个发送
        for wh in fs_list:
            try:
                feishu.send(wh.webhook_url, wh.secret, title, part)
            except Exception as e:
                log.error(f"飞书发送失败 webhook=***: {e}")


def _process_user(user: UserConfig, config: AppConfig, dry_run: bool) -> tuple[bool, int]:
    """处理单个用户，返回 (是否成功, 新增成绩数)。"""
    try:
        password = user.resolve_password()
    except PasswordMissingError as e:
        log.error(f"[{user.account}] 密码缺失: {e}")
        raise

    semester = user.effective_semester(config.semester)
    session, cookies = jwxt.login(user.account, password)
    current = jwxt.fetch_scores(session, cookies, semester)

    if not store.exists(user.account):
        # 初始化分支
        if dry_run:
            log.info(f"[{user.account}] dry-run: 跳过初始化写存储与通知")
        else:
            store.save_atomic(user.account, current)
            notify(user, config.defaults, message="初始化保存当前成绩成功")
        return True, 0

    last = store.load(user.account)
    new = store.diff(current, last)
    if new:
        if dry_run:
            log.info(f"[{user.account}] dry-run: 检测到新成绩 {new}，跳过写存储与通知")
        else:
            store.save_atomic(user.account, current)
            notify(user, config.defaults, new_scores=new)
    else:
        log.info(f"[{user.account}] 没有新成绩")
    return True, len(new)


def run_once(config: AppConfig, target_accounts: Optional[list[str]], dry_run: bool) -> RunResult:
    """执行一次完整遍历。

    Args:
        config: 已加载配置
        target_accounts: 仅运行这些 account；None 表示全部
        dry_run: dry-run 模式
    """
    result = RunResult()
    target_users = config.users
    if target_accounts is not None:
        target_set = set(target_accounts)
        target_users = [u for u in config.users if u.account in target_set]

    for user in target_users:
        try:
            ok, new_count = _process_user(user, config, dry_run)
            if ok:
                result.success_count += 1
                result.new_scores_count += new_count
        except Exception as e:
            log.error(f"[{user.account}] 失败: {e}")
            result.failures.append((user.account, e))
            # 尝试发出错通知；出错通知失败只记日志，不再二次通知
            if not dry_run:
                try:
                    notify(user, config.defaults, message=f"出错: {e}")
                except Exception as notify_err:
                    log.error(f"[{user.account}] 出错通知发送失败: {notify_err}")
    return result


def determine_exit_code(result: RunResult, target_count: int, config_error: bool) -> int:
    """按 §3.1 判定退出码。"""
    if config_error:
        return 1
    if not result.failures:
        return 0
    # 有失败：0 成功 + 全部 NetworkError → 2；否则 3
    if result.success_count == 0 and all(
        isinstance(exc, NetworkError) for _, exc in result.failures
    ):
        return 2
    return 3


def run_watch(config: AppConfig, target_accounts: Optional[list[str]],
              dry_run: bool, interval_minutes: int) -> int:
    """守护模式：循环执行直到收到信号。

    Returns:
        固定返回 0（信号退出为正常路径）
    """
    stop_event = threading.Event()

    def handle_signal(*_):
        log.info("收到退出信号，等待当前轮完成后退出")
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_signal)

    started = datetime.now()
    rounds = 0
    total_success = 0
    total_fail = 0
    total_new = 0

    while not stop_event.is_set():
        round_start = time.time()
        result = run_once(config, target_accounts, dry_run)
        elapsed = time.time() - round_start
        rounds += 1
        total_success += result.success_count
        total_fail += len(result.failures)
        total_new += result.new_scores_count
        log.info(f"round {rounds} done in {elapsed:.1f}s")

        remaining = interval_minutes * 60 - elapsed
        if remaining <= 0:
            log.warning(
                f"single round {elapsed:.1f}s >= interval {interval_minutes}min, "
                "next round starts immediately"
            )
            continue
        # Event.wait 可被信号唤醒
        if stop_event.wait(timeout=remaining):
            break

    total_elapsed = (datetime.now() - started).total_seconds()
    log.info(
        f"守护模式结束 rounds={rounds} success={total_success} "
        f"fail={total_fail} new={total_new} elapsed={total_elapsed:.1f}s"
    )
    return 0
