"""教务系统客户端：登录、成绩抓取、GPA 计算。

内置 1 次立即重试（无 backoff）；验证码识别 3 次独立计数。
"""
from __future__ import annotations

import logging
import re
from io import BytesIO
from typing import Optional

import requests
from bs4 import BeautifulSoup
from PIL import Image

from qfnu_score.captcha import get_ocr_res

log = logging.getLogger(__name__)

# 教务系统地址（HTTP 明文，风险见 README）
BASE_URL = "http://zhjw.qfnu.edu.cn"
RAND_CODE_URL = f"{BASE_URL}/verifycode.servlet"
LOGIN_URL = f"{BASE_URL}/Logon.do?method=logonLdap"
DATA_STR_URL = f"{BASE_URL}/Logon.do?method=logon&flag=sess"
SCORE_URL = f"{BASE_URL}/jsxsd/kscj/cjcx_list"

# 网络层超时（秒）
TIMEOUT = 30
# 网络层重试次数（不含首次，即 1 表示总共最多 2 次）
NETWORK_RETRIES = 1
# 验证码识别最大尝试次数
CAPTCHA_MAX_ATTEMPTS = 3


class JwxtError(Exception):
    """教务系统相关错误基类。"""


class NetworkError(JwxtError):
    """网络不可达/超时。"""


class CaptchaError(JwxtError):
    """验证码识别失败。"""


class LoginError(JwxtError):
    """登录失败（密码错误等）。"""


def _request_with_retry(method: str, url: str, session: requests.Session,
                        cookies: dict, **kwargs):
    """带 1 次立即重试的请求包装。"""
    last_exc: Optional[Exception] = None
    for attempt in range(NETWORK_RETRIES + 1):
        try:
            if method == "GET":
                return session.get(url, cookies=cookies, timeout=TIMEOUT, **kwargs)
            return session.post(url, cookies=cookies, timeout=TIMEOUT, **kwargs)
        except (requests.Timeout, requests.ConnectionError) as e:
            last_exc = e
            log.warning(f"网络请求失败 attempt={attempt + 1}/{NETWORK_RETRIES + 1}: {e}")
    raise NetworkError(str(last_exc))


def _get_initial_session() -> tuple[requests.Session, dict, str]:
    """创建会话并获取初始数据字符串。"""
    session = requests.session()
    try:
        response = _request_with_retry("GET", DATA_STR_URL, session, {})
    except NetworkError:
        raise
    cookies = session.cookies.get_dict()
    return session, cookies, response.text


def _handle_captcha(session: requests.Session, cookies: dict) -> str:
    """获取并识别验证码。"""
    response = _request_with_retry("GET", RAND_CODE_URL, session, cookies)
    if response.status_code != 200:
        raise NetworkError(f"验证码请求失败 status={response.status_code}")
    try:
        image = Image.open(BytesIO(response.content))
    except Exception as e:
        raise NetworkError(f"无法识别图像: {e}")
    return get_ocr_res(image)


def _generate_encoded_string(data_str: str, account: str, password: str) -> str:
    """生成登录所需的 encoded 字符串（迁移自原 main.py）。"""
    res = data_str.split("#")
    code, sxh = res[0], res[1]
    data = f"{account}%%%{password}"
    encoded = ""
    b = 0
    for a in range(len(code)):
        if a < 20:
            encoded += data[a]
            for _ in range(int(sxh[a])):
                encoded += code[b]
                b += 1
        else:
            encoded += data[a:]
            break
    return encoded


def _do_login(session: requests.Session, cookies: dict, account: str,
              password: str, random_code: str, encoded: str) -> requests.Response:
    """执行登录 POST。"""
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/",
        "Upgrade-Insecure-Requests": "1",
    }
    data = {
        "userAccount": account,
        "userPassword": password,
        "RANDOMCODE": random_code,
        "encoded": encoded,
    }
    return _request_with_retry(
        "POST", LOGIN_URL, session, headers=headers, data=data
    )


def login(account: str, password: str) -> tuple[requests.Session, dict]:
    """登录教务系统，返回 (session, cookies)。

    Raises:
        NetworkError: 网络不可达
        CaptchaError: 验证码 3 次识别失败
        LoginError: 密码错误等
    """
    session, cookies, data_str = _get_initial_session()

    last_err: Optional[str] = None
    for attempt in range(CAPTCHA_MAX_ATTEMPTS):
        try:
            random_code = _handle_captcha(session, cookies)
        except NetworkError:
            raise  # 网络问题不重实验证码

        log.info(f"[{account}] 验证码尝试 {attempt + 1}/{CAPTCHA_MAX_ATTEMPTS}: {random_code}")
        encoded = _generate_encoded_string(data_str, account, password)
        response = _do_login(session, cookies, account, password, random_code, encoded)

        if response.status_code != 200:
            raise NetworkError(f"登录请求失败 status={response.status_code}")

        if "验证码错误" in response.text:
            last_err = "验证码错误"
            log.warning(f"[{account}] 验证码错误，重试 {attempt + 1}")
            continue
        if "密码错误" in response.text:
            raise LoginError("用户名或密码错误")
        log.info(f"[{account}] 登录成功")
        return session, cookies

    raise CaptchaError(f"验证码识别失败（{CAPTCHA_MAX_ATTEMPTS} 次）: {last_err}")


def _get_score_page(session: requests.Session, cookies: dict, semester: str) -> str:
    """获取成绩页 HTML。"""
    url = f"{SCORE_URL}?kksj={semester}&kcxz=&kcmc=&xsfs=all"
    response = _request_with_retry("GET", url, session, cookies)
    return response.text


def fetch_scores(session: requests.Session, cookies: dict,
                 semester: str) -> list[list[str]]:
    """抓取指定学期的成绩，返回 [[subject_name, score], ...]。"""
    html = _get_score_page(session, cookies, semester)
    soup = BeautifulSoup(html, "lxml")
    results: list[list[str]] = []

    table = soup.find("table", {"id": "dataList"})
    if table:
        rows = table.find_all("tr")[1:]  # type: ignore[union-attr]
        for row in rows:
            columns = row.find_all("td")
            if len(columns) > 5:
                subject_name = columns[3].get_text(strip=True)
                score = columns[5].get_text(strip=True)
                results.append([subject_name, score])

    log.info(f"获取到 {len(results)} 条成绩")
    return results
