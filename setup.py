from setuptools import find_packages, setup

setup(
    name="qfnu-score",
    version="1.0.0",  # 单一真源，__version__ 通过 importlib.metadata 读取
    description="曲阜师范大学教务系统成绩监控 CLI",
    python_requires=">=3.10,<3.14",  # 受 ddddocr 支持范围约束
    packages=find_packages(exclude=["tests", "tests.*"]),
    install_requires=[
        # 与 requirements.txt 保持一致，开发时同步更新
        "pytz",
        "requests",
        "Pillow",
        "beautifulsoup4",
        "ddddocr",
        "python-dotenv",
        "lxml",
        "pyyaml",
        "click",
    ],
    extras_require={
        "dev": ["pytest>=7.0", "pytest-cov"],
    },
    entry_points={
        "console_scripts": [
            "qfnu-score=qfnu_score.cli:main",
        ],
    },
)
