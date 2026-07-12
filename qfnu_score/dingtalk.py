"""钉钉 webhook 发送，日志脱敏。

迁移自原 dingtalk.py，加 token/secret 脱敏日志。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.parse

import requests

log = logging.getLogger(__name__)

# 钉钉单条消息正文上限（留余量到 18000）
MAX_CONTENT_LEN = 18000


def _mask(value: str) -> str:
    """脱敏：长度 > 10 时前 6 位 + *** + 后 4 位；否则 ***。"""
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


def send(token: str, secret: str, title: str, content: str) -> dict:
    """发送一条钉钉文本消息。

    Args:
        token: 钉钉 webhook 的 access_token
        secret: 钉钉机器人加签密钥
        title: 消息标题（并入正文）
        content: 消息正文

    Returns:
        钉钉接口返回的 JSON 字典
    """
    url = f"https://oapi.dingtalk.com/robot/send?access_token={token}"
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code).decode("utf-8").strip())
    url = f"{url}&timestamp={timestamp}&sign={sign}"

    headers = {"Content-Type": "application/json"}
    payload = {"msgtype": "text", "text": {"content": f"{title}\n{content}"}}

    log.info(f"钉钉请求 token(脱敏): {_mask(token)} sign(脱敏): {sign[:10]}***{sign[-6:]}")
    log.debug(f"钉钉请求载荷: {json.dumps(payload, ensure_ascii=False)}")

    response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
    try:
        data = response.json()
    except Exception as e:
        log.error(f"钉钉响应解析失败: {e} 原始: {response.text[:200]}")
        raise

    if response.status_code == 200 and data.get("errcode") == 0:
        log.info("钉钉发送成功")
    else:
        log.error(
            f"钉钉发送失败 code={data.get('errcode')} msg={data.get('errmsg')}"
        )
    return data
