"""飞书 webhook 发送，日志脱敏。

迁移自原 feishu.py，修复了未配置 webhook 时报错的问题。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Optional

import requests

log = logging.getLogger(__name__)

# 飞书单条消息正文上限
MAX_CONTENT_LEN = 30000


def _mask(value: str) -> str:
    if not value:
        return "***"
    if len(value) > 10:
        return f"{value[:6]}***{value[-4:]}"
    return "***"


def _mask_url(url: str) -> str:
    if not url:
        return "***"
    if len(url) > 50:
        return f"{url[:30]}***{url[-10:]}"
    if len(url) > 16:
        return f"{url[:10]}***{url[-6:]}"
    return "***"


def send(webhook_url: str, secret: str, title: str, content: str) -> dict:
    """发送一条飞书 post 消息。

    Args:
        webhook_url: 飞书机器人 webhook URL
        secret: 飞书机器人签名校验密钥
        title: 消息标题
        content: 消息正文
    """
    timestamp = str(int(time.time()))
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    sign = base64.b64encode(hmac_code).decode("utf-8")

    headers = {"Content-Type": "application/json"}
    msg = {
        "timestamp": timestamp,
        "sign": sign,
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": title,
                    "content": [[{"tag": "text", "text": content}]],
                }
            }
        },
    }

    log.info(f"飞书请求 webhook(脱敏): {_mask_url(webhook_url)}")
    log.debug(f"飞书请求载荷: {json.dumps({k: v for k, v in msg.items() if k != 'sign'}, ensure_ascii=False)}")

    response = requests.post(webhook_url, headers=headers, data=json.dumps(msg), timeout=10)
    try:
        data = response.json()
    except Exception as e:
        log.error(f"飞书响应解析失败: {e} 原始: {response.text[:200]}")
        raise

    if response.status_code == 200 and data.get("code") == 0:
        log.info("飞书发送成功")
    else:
        log.error(
            f"飞书发送失败 code={data.get('code')} msg={data.get('msg')}"
        )
    return data
