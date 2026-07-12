"""ddddocr 单例包装。

进程首次使用时加载一次，多用户共享，进程生命周期内不 reload。

采用延迟导入：模块导入时不加载 ddddocr（模型加载耗时），首次调用 get_ocr_res 时才加载。
这样 --version、--help 等不需要 OCR 的命令也能快速响应，且单元测试 mock 时无需安装 ddddocr。
"""
from __future__ import annotations

import threading
from typing import Any, Optional

from PIL import Image

_ocr: Optional[Any] = None
_lock = threading.Lock()


def _get_ocr() -> Any:
    """延迟加载 ddddocr 实例（首次调用时加载，之后复用）。"""
    global _ocr
    if _ocr is None:
        with _lock:
            if _ocr is None:
                import ddddocr  # 延迟导入：仅在实际需要识别时加载
                _ocr = ddddocr.DdddOcr()
    return _ocr


def get_ocr_res(image: Image.Image) -> str:
    """识别验证码图片，返回识别结果字符串。"""
    ocr = _get_ocr()
    return ocr.classification(image)
