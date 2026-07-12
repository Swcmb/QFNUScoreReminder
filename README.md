# QFNUScoreReminder

![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)

**曲阜师范大学教务系统成绩监控 CLI**

检测到新成绩后通过钉钉 / 飞书机器人通知。支持多用户、多 webhook、可配置探测间隔、一次性 / 守护进程两种模式。

---

## 特性

- **CLI 工具**：`qfnu-score` 命令，安装后即可使用
- **多用户**：一份配置文件管理多个教务系统账号
- **多钉钉 / 多飞书通知**：每个用户可配独立 webhook，未配时回退到全局默认
- **双模式**：默认一次性执行；`--watch` 进入守护进程按间隔循环探测
- **可配置间隔**：通过 `config.yaml` 或 `--interval` 设置，建议 ≥ 5 分钟
- **原子写入**：成绩状态按用户分文件存储，进程中断不会产生半截 JSON
- **环境变量密码**：支持 `ENV:VAR_NAME` 引用，密码不进仓库

---

## 安装

```bash
git clone https://github.com/Swcmb/QFNUScoreReminder.git
cd QFNUScoreReminder
git checkout feat/cli-multiuser

pip install -r requirements.txt
pip install -e .                    # 注册 qfnu-score 命令
cp config.example.yaml config.yaml  # 复制模板
```

要求 Python 3.10–3.13（受 `ddddocr` 支持范围约束）。

---

## 配置

编辑 `config.yaml`：

```yaml
# 全局探测间隔（分钟），仅 --watch 模式生效
# 建议 >= 5，过小可能触发教务系统账号锁定
interval_minutes: 5

# 默认学期
semester: "2024-2025-2"

# 全局默认通知（用户未配时回退使用）
defaults:
  dingtalk:
    - token: "global_dingtalk_token"
      secret: "global_dingtalk_secret"
  feishu:
    - webhook_url: "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
      secret: "global_feishu_secret"

# 用户列表
users:
  - account: "20240001"
    password: "ENV:USER_20240001_PASSWORD"   # 推荐用环境变量
  - account: "20240002"
    password: "ENV:USER_20240002_PASSWORD"
    dingtalk:                                 # 用户级覆盖默认
      - token: "user_dingtalk_token"
        secret: "user_dingtalk_secret"
```

### 钉钉机器人获取

1. 钉钉群 → 群设置 → 机器人 → 添加自定义 webhook 机器人
2. 安全设置选「加签」，记录 `webhook` 与 `secret`
3. webhook URL 中 `access_token=xxx` 的 `xxx` 即为 `token`

### 飞书机器人获取

1. 飞书开放平台 → 创建自定义机器人
2. 记录 `webhook` 与签名校验 `secret`

### 密码处理

`password` 字段支持两种写法：

- **明文**（仅本地测试）：`password: "my_password"`
- **环境变量引用**（生产推荐）：`password: "ENV:USER_20240001_PASSWORD"`，启动前设置 `export USER_20240001_PASSWORD=your_real_password`

也可用 `.env` 文件，CLI 启动时会自动加载（`python-dotenv`）。

---

## 使用

### 一次性执行（探测一次后退出）

```bash
qfnu-score
```

### 守护进程模式（按间隔循环探测）

```bash
qfnu-score --watch
qfnu-score --watch --interval 10     # 覆盖配置间隔
```

`Ctrl+C` 优雅退出，输出累计统计（成功数 / 失败数 / 新增成绩数 / 总耗时 / 轮数）。

### 仅运行指定用户

```bash
qfnu-score --user 20240001
qfnu-score --user 20240001 --user 20240002   # 多个
```

### Dry-run（只检测不通知、不写存储）

```bash
qfnu-score --dry-run
```

### 详细日志

```bash
qfnu-score -v          # DEBUG 级
qfnu-score --verbose
```

### 查看版本

```bash
qfnu-score --version
```

---

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 全部用户成功 |
| 1 | 配置错误（文件缺失/格式错误/参数冲突/`--user` 不存在） |
| 2 | 系统级不可达（0 用户成功 + 全部为网络错误） |
| 3 | 部分或全部用户级失败（登录/验证码等） |

> `--watch` 模式收到信号退出时固定返回 0。

---

## 部署建议

### systemd（Linux 服务器）

```ini
# /etc/systemd/system/qfnu-score.service
[Unit]
Description=QFNU Score Reminder
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/QFNUScoreReminder
EnvironmentFile=/opt/QFNUScoreReminder/.env
ExecStart=/opt/QFNUScoreReminder/.venv/bin/qfnu-score --watch
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now qfnu-score
# 查看日志
journalctl -u qfnu-score -f
```

### 日志滚动

`--watch` 长跑建议重定向到文件并配合 `logrotate`：

```bash
qfnu-score --watch >> /var/log/qfnu-score.log 2>&1
```

---

## 开发

### 运行测试

```bash
pip install -e ".[dev]"
pytest
```

### 项目结构

```
qfnu_score/
├── cli.py          # click 入口
├── config.py       # 配置加载与校验
├── monitor.py      # 编排逻辑
├── jwxt.py         # 教务系统客户端
├── captcha.py      # ddddocr 单例
├── dingtalk.py     # 钉钉发送
├── feishu.py       # 飞书发送
└── store.py        # 成绩持久化
tests/              # 单元测试
docs/superpowers/specs/  # 设计文档
```

---

## 风险声明

- 教务系统为 HTTP 明文，密码传输有被嗅探风险，建议在可信网络环境运行
- 短间隔守护模式 + 密码错误配置可能导致账号被教务系统锁定，建议 `interval_minutes` ≥ 5
- 登录流程变更（如新增滑块、改 CAS SSO）视为超出本期范围

---

## 更新日志

### 2026-07-12 — CLI 化与多用户支持

- 重构为 Python CLI 工具 `qfnu-score`，移除 GitHub Actions 工作流
- 支持多用户配置（YAML），每用户独立成绩状态文件
- 支持多钉钉 / 多飞书 webhook，用户级 + 全局默认回退
- 双模式：一次性执行与 `--watch` 守护进程
- 密码支持 `ENV:` 环境变量引用
- 原子写入、日志脱敏、超长消息拆分
- 完整单元测试覆盖

### 2025-01-17

- 更新了获取全部学期的总学分和平均绩点
- 更新了计算本学期绩点
- 新增 `SEMESTER` 环境变量
