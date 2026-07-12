"""qfnu_score 包标识。

通过 importlib.metadata 暴露 __version__，与 setup.py 的 version 字段保持单一真源。
"""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("qfnu-score")
except PackageNotFoundError:  # 未安装（如直接 python -m qfnu_score）
    __version__ = "0.0.0+unknown"
