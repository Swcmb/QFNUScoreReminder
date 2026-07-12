"""click CLI 入口：参数解析与模式分发。"""
from __future__ import annotations

import logging
import sys
from typing import Optional

import click
from dotenv import load_dotenv

from qfnu_score import __version__, monitor
from qfnu_score.config import ConfigError, load_config


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )


def _filter_users(config, accounts: tuple[str, ...]) -> Optional[list[str]]:
    """返回存在的 account 列表，或 None 表示全部。

    Raises:
        ConfigError: 传入不存在的 account
    """
    if not accounts:
        return None
    # 去重保序
    seen: set[str] = set()
    unique: list[str] = []
    for a in accounts:
        if a not in seen:
            seen.add(a)
            unique.append(a)
    # 校验存在性
    known = {u.account for u in config.users}
    unknown = [a for a in unique if a not in known]
    if unknown:
        raise ConfigError(
            f"以下 --user 不存在: {unknown}。可用: {sorted(known)}"
        )
    return unique


@click.command()
@click.option("--config", "config_path", default="config.yaml", show_default=True,
              help="配置文件路径")
@click.option("--interval", "interval", type=int, default=None,
              help="覆盖配置中的 interval_minutes（分钟），仅 --watch 模式生效")
@click.option("--watch", is_flag=True, default=False, help="守护进程模式")
@click.option("--once", is_flag=True, default=False, help="显式表示跑一次")
@click.option("--user", "users", multiple=True,
              help="仅运行指定学号（可多次传）")
@click.option("--dry-run", is_flag=True, default=False,
              help="检测并打印 diff，但不发送通知、不写存储")
@click.option("--verbose", "-v", is_flag=True, default=False, help="启用 DEBUG 日志")
@click.version_option(__version__, prog_name="qfnu-score")
def main(config_path: str, interval: Optional[int], watch: bool, once: bool,
         users: tuple[str, ...], dry_run: bool, verbose: bool) -> None:
    """曲阜师范大学教务系统成绩监控 CLI。"""
    _setup_logging(verbose)
    load_dotenv()  # 从 .env 加载环境变量，便于本地调试

    # 参数互斥与边界校验
    if watch and once:
        click.echo("错误：--watch 与 --once 不能同时传", err=True)
        sys.exit(1)
    if interval is not None and not watch:
        click.echo("错误：--interval 仅在 --watch 模式生效", err=True)
        sys.exit(1)
    if interval is not None and interval < 1:
        click.echo("错误：--interval 必须 >= 1", err=True)
        sys.exit(1)

    try:
        config = load_config(config_path)
    except ConfigError as e:
        click.echo(f"配置错误: {e}", err=True)
        sys.exit(1)

    try:
        target_accounts = _filter_users(config, users)
    except ConfigError as e:
        click.echo(f"错误: {e}", err=True)
        sys.exit(1)

    # --watch 模式下 interval 解析
    if watch:
        effective_interval = interval if interval is not None else config.interval_minutes
        if effective_interval is None or effective_interval < 1:
            click.echo(
                "错误：--watch 模式下需通过 --interval 或配置 interval_minutes 提供有效间隔",
                err=True,
            )
            sys.exit(1)
    else:
        effective_interval = None

    target_count = len(config.users if target_accounts is None else target_accounts)

    if watch:
        code = monitor.run_watch(
            config, target_accounts, dry_run, effective_interval
        )
        sys.exit(code)

    # 一次性模式
    result = monitor.run_once(config, target_accounts, dry_run)
    code = monitor.determine_exit_code(result, target_count, config_error=False)
    if result.failures:
        click.echo(
            f"完成: success={result.success_count} fail={len(result.failures)} "
            f"new={result.new_scores_count}",
            err=True,
        )
    else:
        click.echo(
            f"完成: success={result.success_count} new={result.new_scores_count}"
        )
    sys.exit(code)


if __name__ == "__main__":
    main()
