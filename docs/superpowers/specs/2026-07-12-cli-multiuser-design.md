# QFNUScoreReminder CLI 化与多用户支持 — 设计文档

- **文档日期**：2026-07-12
- **目标分支**：`feat/cli-multiuser`
- **方案选型**：方案 A — 配置驱动单体 CLI
- **状态**：修订版 v2（已根据 DocReview 反馈修正）

---

## 1. 目标与非目标

### 1.1 目标

- 将现有单用户 GitHub Actions 脚本改造为可本地/服务器运行的 Python CLI 工具 `qfnu-score`。
- 支持一次性执行与 `--watch` 守护进程两种模式，守护模式按可配置间隔循环探测。
- 支持多用户配置（YAML），每用户独立教务系统账号与成绩状态。
- 支持多钉钉与多飞书 webhook 通知，采用「用户级 + 全局默认回退」映射策略。
- 移除 GitHub Actions 定时任务，运行责任完全交给用户本地/服务器。

> 交付动作（推送至 GitHub 新分支）见第 11 节，不作为产品功能目标。

### 1.2 非目标

- 不做 Web UI、不引入数据库。
- 不重写验证码识别（继续使用 `ddddocr`）。
- 不做通知器插件抽象（当前仅钉钉 + 飞书，YAGNI）。
- 不做异步/多线程并发（用户量小，串行足够）。
- 不做历史成绩趋势分析、不接入其它教务系统。
- 不做账号锁定自动停跑机制（见 §8 风险声明）。
- 不做登录流程变更的自动适配（如教务系统改 CAS SSO，视为超出本期范围）。

---

## 2. 包结构

迁移完成后删除旧脚本 `main.py` / `dingtalk.py` / `feishu.py` / `captcha_ocr.py`，并删除 `.github/workflows/auto-commit.yml`。

```
/workspace/
├── qfnu_score/
│   ├── __init__.py          # 包标识，通过 importlib.metadata 暴露 __version__
│   ├── __main__.py          # 支持 python -m qfnu_score
│   ├── cli.py               # click 入口，参数解析与模式分发
│   ├── config.py            # 加载并校验 config.yaml
│   ├── monitor.py           # 编排：遍历用户、对比成绩、触发通知
│   ├── jwxt.py              # 教务系统客户端（登录、取成绩、算 GPA，内置 1 次重试）
│   ├── captcha.py           # ddddocr 单例包装
│   ├── dingtalk.py          # 钉钉发送（迁移自原 dingtalk.py）
│   ├── feishu.py            # 飞书发送（迁移自原 feishu.py）
│   └── store.py             # 按学号持久化成绩状态（原子写入）
├── tests/                   # 单元测试
│   ├── test_config.py
│   ├── test_store.py
│   ├── test_monitor.py
│   └── test_cli.py          # CLI 参数解析、模式互斥、--version、--user 过滤与不存在报错、--interval 边界、去重
├── data/                    # 运行时生成，加入 .gitignore
│   └── <account>.json
├── config.example.yaml
├── setup.py
├── requirements.txt
├── .gitignore               # 追加 config.yaml、data/
└── README.md
```

**职责边界**：

| 模块 | 单一职责 | 依赖 |
|---|---|---|
| `cli.py` | 参数解析、模式分发、退出码、`load_dotenv()` | click, dotenv, config, monitor |
| `config.py` | 加载、校验配置；返回强类型对象 | pyyaml |
| `monitor.py` | 编排单次/循环、用户遍历、通知回退、退出码判定 | jwxt, store, dingtalk, feishu |
| `jwxt.py` | 教务系统登录、成绩抓取、GPA 计算；内置 1 次立即重试（无 backoff） | requests, bs4, captcha |
| `captcha.py` | ddddocr 单例（进程生命周期内不 reload） | ddddocr |
| `dingtalk.py` | 钉钉 webhook 发送，日志脱敏 | requests |
| `feishu.py` | 飞书 webhook 发送，日志脱敏 | requests |
| `store.py` | 按学号读写 `data/<account>.json`，原子替换 | 无外部依赖 |

---

## 3. CLI 参数

命令：`qfnu-score [OPTIONS]`

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `--config PATH` | 路径 | `config.yaml` | 配置文件路径；不存在时报错退出码 1，提示复制 `config.example.yaml` |
| `--interval N` | 整数（分钟） | 取自配置 | 覆盖配置 `interval_minutes`；**仅在 `--watch` 模式生效，其它模式传入报错退出码 1** |
| `--watch` | 标志 | False | 守护进程模式，按 `interval` 循环探测 |
| `--once` | 标志 | False | 显式表示跑一次（与默认行为一致，便于脚本化） |
| `--user ACCOUNT` | 字符串 | 全部 | 仅运行指定学号（可多次传）；传入不存在的 account 报错退出码 1 并列出可用 account；重复传同一 account 自动去重 |
| `--dry-run` | 标志 | False | 检测并打印 diff，但**不发送任何通知、不写存储**（与 `store.save_atomic` 一并跳过） |
| `--verbose / -v` | 标志 | False | 启用 DEBUG 级日志 |
| `--version` | 标志 | — | 通过 `importlib.metadata.version("qfnu-score")` 读取后输出 |

**模式互斥**：`--watch` 与 `--once` 不能同时传，违反时报错退出码 1。

**`--interval` 边界**：

- 非 `--watch` 模式下传入 `--interval` → 报错退出码 1。
- `--watch` 模式下既不传 `--interval`、配置中也无 `interval_minutes` → 报错退出码 1。
- `--interval` 必须 ≥ 1（与 `interval_minutes` 同约束），否则报错退出码 1。

### 3.1 退出码

| 码 | 含义 | 判定逻辑 |
|---|---|---|
| 0 | 成功 | `success_count == len(target_users)`（全部成功，无 failures；`--dry-run` 模式同此判定，详见下方注） |
| 1 | 配置错误 | 文件缺失/格式错误/参数冲突/`--user` 不存在/`--interval` 误用 |
| 2 | 系统级不可达 | 0 个用户成功，且失败原因均为 `NetworkError`（教务系统不可达） |
| 3 | 部分或全部用户级失败 | 至少 1 个用户成功；**或** 0 个用户成功但失败原因为 `LoginError`/`CaptchaError` 等非系统级错误 |

判定伪代码（在 5.1 流程末尾执行）：

```python
if config_error: exit(1)
# 全部成功（无失败）才 exit(0)；有失败按失败类型决定 2 或 3
if not failures: exit(0)
# failures 元素为 (account, exception) 二元组，需解包
# exit(2) 需同时满足：0 成功 + 全部为 NetworkError（系统级不可达）
if success_count == 0 and all(isinstance(exc, NetworkError) for _, exc in failures):
    exit(2)
else:
    exit(3)
```

> 注：`--watch` 模式收到信号退出时固定返回 0（守护进程的正常退出路径），`loop_stats` 仅用于日志统计，不影响退出码。`--dry-run` 模式下，配置错误（exit 1）仍生效；用户级失败（登录/网络等）仍按上表判定，不因 `--dry-run` 而强制 exit(0)。

**守护模式信号处理**：

- 目标平台 Linux/macOS，捕获 `SIGINT`/`SIGTERM`。
- Windows 上仅 `SIGINT`（`Ctrl+C`）生效，`SIGTERM` 不生效，文档与 README 同步声明。
- 收到信号后：当前用户跑完后优雅退出，输出本次累计统计。

---

## 4. 配置文件 Schema

文件：`config.yaml`（运行时配置，加入 `.gitignore`）；`config.example.yaml` 为模板，纳入版本控制。

```yaml
# 全局探测间隔（分钟），仅 --watch 模式生效，可被 --interval 覆盖
interval_minutes: 5

# 默认学期，可被单用户覆盖
semester: "2024-2025-2"

# 全局默认通知渠道：用户未配对应渠道时回退使用
defaults:
  dingtalk:
    - token: "global_token_1"
      secret: "global_secret_1"
  feishu:
    - webhook_url: "https://open.feishu.cn/open-apis/bot/v2/hook/global1"
      secret: "global_feishu_secret_1"

# 用户列表
users:
  - account: "20240001"
    password: "plain_password_only_for_local_test"   # 明文，仅本地测试
    semester: "2024-2025-2"                          # 可选，缺省取顶层 semester
    dingtalk:                                        # 可选，覆盖默认
      - token: "user_token_1"
        secret: "user_secret_1"
    feishu:                                          # 可选，覆盖默认
      - webhook_url: "https://open.feishu.cn/open-apis/bot/v2/hook/user1"
        secret: "user_feishu_secret_1"
  - account: "20240002"
    password: "ENV:USER_20240002_PASSWORD"           # 环境变量引用，生产推荐
    # 未配 dingtalk/feishu，回退到 defaults
```

### 4.1 密码处理

`password` 字段统一判定规则：

```python
if password.startswith("ENV:"):
    var_name = password[4:]
    actual = os.environ.get(var_name)
    if actual is None:
        raise PasswordMissingError(account, var_name)
    return actual
else:
    return password  # 明文，仅本地测试用
```

- **明文**：直接写密码字符串（仅用于本地测试，文档强烈不推荐）。
- **环境变量引用**：值必须以 `ENV:` 开头，后接变量名；变量不存在视为密码缺失，跳过该用户并记录 ERROR。
- 推荐生产用法：所有用户密码以 `ENV:USER_<ACCOUNT>_PASSWORD` 形式配置，密码不进仓库。

### 4.2 配置校验规则

- 顶层 `interval_minutes` 必须 ≥ 1（`--watch` 模式下缺失则报错退出码 1）。
- `users` 至少 1 个；每个用户 `account` 与 `password` 必填。
- `account` 必须匹配 `^[A-Za-z0-9_-]{4,32}$`，否则报错退出码 1（防路径注入）。
- `semester` 格式必须匹配 `^\d{4}-\d{4}-[1-2]$`，否则报错退出码 1。
- `defaults` 整体可选；若 `defaults.dingtalk` 或 `defaults.feishu` 存在，其每一项的字段（token/secret 或 webhook_url/secret）必填。
- 同一 `account` 在 `users` 中重复时报错。
- **无通知渠道告警**：若某用户既无用户级 dingtalk/feishu、且 `defaults` 中对应渠道也为空，配置加载阶段记 WARNING；运行时该用户有新成绩但无渠道可发时记 ERROR。

---

## 5. 运行流程

### 5.1 一次性模式（默认）

```python
config = load_config(config_path)        # 失败 → exit(1)
target_users = filter_users(config.users, args.user)  # 不存在 account → exit(1)；内部先对 args.user 去重再过滤，计数基准为去重后的 target_users

success_count = 0
failures = []  # [(account, exception), ...]

for user in target_users:
    try:
        session = jwxt.login(user.account, user.password, user.semester)
        current = jwxt.fetch_scores(session)
        if not store.exists(user.account):
            # 初始化分支：保存当前成绩，发"初始化成功"通知，不视为新成绩
            if not args.dry_run:
                store.save_atomic(user.account, current)
                notify(user, config.defaults, message="初始化保存当前成绩成功")
            else:
                log_info("dry-run: 跳过初始化写存储与通知")
            success_count += 1
            continue
        last = store.load(user.account)
        new_scores = diff(current, last)
        if new_scores:
            if not args.dry_run:
                store.save_atomic(user.account, current)
                notify(user, config.defaults, new_scores=new_scores)
            else:
                log_info(f"dry-run: 检测到新成绩 {new_scores}，跳过写存储与通知")
        success_count += 1
    except Exception as e:
        log_error(account, e)
        failures.append((user.account, e))
        # 尝试发出错通知；出错通知失败只记日志，不再二次通知
        if not args.dry_run:
            try:
                notify(user, config.defaults, message=f"出错: {e}")
            except Exception as notify_err:
                log_error(f"出错通知发送失败: {notify_err}")

# 退出码判定（见 §3.1 伪代码）
```

### 5.2 守护模式（`--watch`）

```python
config = load_config(...)
loop_stats = {"started": now, "rounds": 0, "total_success": 0, "total_fail": 0, "total_new": 0}
stop_event = threading.Event()

def handle_signal(*_):
    stop_event.set()

signal.signal(signal.SIGINT, handle_signal)
if hasattr(signal, "SIGTERM"):
    signal.signal(signal.SIGTERM, handle_signal)

while not stop_event.is_set():
    round_start = now
    run_once(users, ...)            # 同 5.1，累加 loop_stats
    elapsed = now - round_start
    loop_stats["rounds"] += 1
    log_info(f"round {loop_stats['rounds']} done in {elapsed}s")
    if elapsed >= interval_minutes * 60:
        log_warning(f"single round {elapsed}s >= interval {interval_minutes}min, next round starts immediately")
        continue
    # 用 Event.wait 替代 time.sleep：收到信号时 set() 立即唤醒
    remaining = interval_minutes * 60 - elapsed
    if stop_event.wait(timeout=remaining):
        break  # 信号到达

log_info(stats_summary(loop_stats))  # 成功数/失败数/新增成绩数/总耗时/轮数
exit(0)
```

**单次耗时 > interval 策略**：本次结束后立即开始下一轮（不 sleep），并记 WARNING。
**`loop_stats` 仅用于日志统计**，不影响退出码；`--watch` 收到信号退出固定返回 0。

---

## 6. 成绩存储

- 路径：`data/<account>.json`。
- `account` 已在 §4.2 白名单校验，杜绝路径注入。
- 结构：与现有 `scores.json` 一致，即 `[[subject_name, score], ...]`，便于迁移历史数据。
- `data/` 目录加入 `.gitignore`，不进版本控制。
- **原子写入**：`store.save_atomic()` 先写 `data/.<account>.json.tmp` 再 `os.replace` 替换目标文件，杜绝进程中断产生半截 JSON。
- **初始化语义**：文件不存在视为初始化，保存当前成绩并发"初始化成功"通知，不视为新成绩（见 §5.1）。

---

## 7. 通知回退逻辑

`monitor.resolve_notifiers(user, defaults)` 返回 `(dingtalk_list, feishu_list)`：

- 若 `user.dingtalk` 非空 → 用 user 的；否则 → 用 `defaults.dingtalk`（可能为空）。
- `feishu` 同理，两渠道独立判断。
- 任一列表为空则该渠道不发。
- 列表内可有多项，**逐个发送**，单个 webhook 失败仅记录错误，不影响列表内其它 webhook。

### 7.1 通知失败与退出码的关系

- **通知失败不影响用户成功状态**。用户成绩已成功探测并保存即视为成功，仅记 ERROR 日志。
- 出错通知自身失败只记 ERROR，不再二次通知（防递归）。

### 7.2 `notify` 函数签名

```python
def notify(
    user: UserConfig,
    defaults: DefaultsConfig,
    *,
    message: str | None = None,        # 直接正文（初始化/出错通知用）
    new_scores: list | None = None,    # 新成绩列表（新成绩通知用）
) -> None
```

- `message` 与 `new_scores` 互斥；同时传报错。
- `new_scores` 非空时按 §7.3 模板渲染为新成绩正文。
- 内部调用 `resolve_notifiers(user, defaults)` 解析两渠道列表后逐个发送。

### 7.3 日志脱敏

- 所有 webhook token/secret 在日志中脱敏：长度 > 10 时打印 `前6位***后4位`，否则打印 `***`。
- 飞书 webhook_url 同样脱敏（前 30 位 + `***` + 后 10 位）。

### 7.4 消息格式

- 标题固定："成绩监控通知"
- 正文统一前缀：
  ```
  学号: <account>
  <正文>
  ```
- **新成绩正文**：
  ```
  发现新成绩！
  科目: <name1>
  成绩: <score1>
  科目: <name2>
  成绩: <score2>
  ```
- **初始化通知正文**：`初始化保存当前成绩成功`
- **出错通知正文**：`出错: <异常消息>`
- **超长拆分**：若正文 > 18000 字符（钉钉上限约 20000，留余量），拆分为多条按顺序发送，每条标题加 "(N/M)" 后缀。

---

## 8. 错误处理与边界

| 场景 | 处理 |
|---|---|
| `config.yaml` 不存在 | 提示复制 `config.example.yaml`，退出码 1 |
| `config.yaml` 格式错误 | 打印具体行/字段错误，退出码 1 |
| 用户密码缺失（env 未设） | 跳过该用户，记录 ERROR，继续其他用户；计入 `failures` |
| 验证码识别失败 | 与网络重试**相互独立、互不嵌套**：验证码识别最多 3 次（针对 ddddocr 识别结果或教务系统返回"验证码错误"），3 次失败后抛 `CaptchaError`，不再触发网络层重试；捕获后尝试发"验证码失败"通知，计入 `failures` |
| 登录密码错误 | 抛 `LoginError`，捕获后尝试发"密码错误"通知，计入 `failures` |
| 网络请求超时 | `requests` timeout 由原 1000s 改为 30s；`jwxt` 内部立即重试 1 次（无 backoff）后抛 `NetworkError`，计入 `failures` |
| `data/` 目录不存在 | 自动创建 |
| 守护模式收到信号 | 当前用户跑完后优雅退出，输出累计统计 |
| 飞书/钉钉 webhook 未配置 | 调用前判空，不发 |
| ddddocr 模型加载 | 进程启动时加载一次，多用户共享 `captcha` 模块单例；进程生命周期内不 reload，建议用 systemd/supervisor 定期重启 |
| 多用户并发 | 默认串行；不保证线程安全 |
| 守护模式单次耗时 ≥ interval | 立即开始下一轮，记 WARNING |
| 通知发送失败 | 仅记 ERROR，不影响用户成功状态、不影响退出码 |
| account 含特殊字符 | §4.2 白名单校验拒绝 |
| 环境变量修改 | 进程启动时一次性读取，修改后需重启进程 |

**风险声明（不自动处理）**：

- 教务系统为 HTTP 明文，密码传输有被嗅探风险，建议在可信网络环境运行。
- 短间隔守护模式 + 密码错误配置可能导致账号被教务系统锁定，此风险由用户自行承担；建议 `interval_minutes` ≥ 5。
- 登录流程变更（如新增滑块、改 CAS SSO）视为超出本期范围。

---

## 9. 依赖与安装

### 9.1 `requirements.txt`

```
pytz
requests
Pillow
beautifulsoup4
ddddocr
python-dotenv        # 保留：cli.py 入口处 load_dotenv()，便于本地 .env 调试
lxml
pyyaml               # 新增
click                # 新增
```

**配置优先级**：CLI 参数 > 环境变量（含 `.env`）> 配置文件。

### 9.2 安装

```bash
pip install -r requirements.txt
pip install -e .                    # 注册 qfnu-score 命令
cp config.example.yaml config.yaml  # 编辑后使用
qfnu-score --version
```

### 9.3 `setup.py` 关键字段

```python
setup(
    name="qfnu-score",
    version="1.0.0",                          # 单一真源
    python_requires=">=3.10,<3.14",           # 受 ddddocr 支持范围约束
    install_requires=[
        # 与 §9.1 requirements.txt 保持一致，开发时同步更新
        "pytz", "requests", "Pillow", "beautifulsoup4",
        "ddddocr", "python-dotenv", "lxml", "pyyaml", "click",
    ],
    entry_points={
        "console_scripts": [
            "qfnu-score=qfnu_score.cli:main",
        ],
    },
)
```

`__version__` 通过 `importlib.metadata.version("qfnu-score")` 读取，与 `setup.py` 的 `version` 字段保持单一真源。

---

## 10. 日志规范

- 默认 INFO 级，输出到 stdout；`--verbose` 启用 DEBUG。
- 守护模式长跑建议用户重定向到文件并配合 logrotate（README 说明）。
- 格式：`%(asctime)s - %(levelname)s - %(message)s`。
- 所有 token/secret/URL 必须脱敏（见 §7.2）。

---

## 11. 测试策略

- **单元测试**（`tests/`）：
  - `test_config.py`：配置加载与校验规则（含 `ENV:` 前缀、account 白名单、semester 正则、重复 account）。
  - `test_store.py`：原子写入、初始化分支、diff 计算。
  - `test_monitor.py`：通知回退（用户级 > 默认）、退出码判定、`--user` 过滤、`--dry-run`。
  - `test_cli.py`：CLI 参数解析、模式互斥（`--watch` 与 `--once`）、`--version`、`--user` 过滤与不存在报错、`--user` 去重、`--interval` 边界（非 `--watch` 模式报错、≥1 约束）。
- **Mock**：`jwxt`/`dingtalk`/`feishu` 在单元测试中全部 mock，不发起真实网络请求。
- **集成测试**：手动执行验收清单 A1-A10。
- **回归基线**：迁移前在 `main` 分支打 tag `pre-cli-v1` 作为回退点。

---

## 12. 验收清单

| 编号 | 项 | 验证方式 |
|---|---|---|
| A1 | `qfnu-score --version` 输出 `1.0.0` | 自动（`tests/test_cli.py::test_version`） |
| A2 | `config.yaml` 缺失时报错并提示复制模板，退出码 1 | 自动（`tests/test_config.py::test_missing_file`） |
| A3 | 单用户配置可登录并完成一次探测，日志含"开始处理成绩" | 手动（需真实教务系统账号） |
| A4 | 手动修改 `data/<account>.json` 删除一条记录后运行，钉钉与飞书均收到含该条成绩的通知 | 手动（需真实 webhook） |
| A5 | 用户级钉钉列表优先于全局默认（用不同 token 区分，验证消息来自用户级 webhook） | 自动（`tests/test_monitor.py::test_user_notifier_overrides_default`，mock webhook 后断言调用参数） |
| A6 | `--watch --interval 1 -v` 启动后观察 ≥3 轮，每轮日志出现 `round N done in Xs`；休眠期间发送 `Ctrl+C` 能在当前轮结束后立即退出（验证 `Event.wait` 响应信号） | 手动（需观察多轮日志与信号响应） |
| A7 | `Ctrl+C` 后输出含字段：成功数、失败数、新增成绩数、总耗时、轮数 | 手动（需观察实际进程退出） |
| A8 | `--user 20240001` 仅运行该用户；`--user 99999999`（不存在）报错退出码 1 并列出可用 account；`--user 20240001 --user 20240001` 等价于单次传入（去重） | 自动（`tests/test_cli.py::test_user_filter`、`test_user_not_found`、`test_user_dedup`） |
| A9 | `--dry-run` 修改 `data/<account>.json` 后运行，日志打印 diff 但钉钉/飞书无消息、存储不变 | 自动（`tests/test_monitor.py::test_dry_run_no_side_effects`，mock 后断言 store 与 notifier 未被调用） |
| A10 | 多用户中某用户密码错误，该用户失败计入日志但其他用户正常完成，退出码 3 | 自动（`tests/test_monitor.py::test_partial_failure_exit_code`） |

> 注：推送至 `feat/cli-multiuser` 分支（原计划 A11）属于交付动作，见 §13，不列入功能验收清单。

---

## 13. 迁移与发布

1. 在 `main` 分支基础上打 tag `pre-cli-v1` 作为回退点。
2. 从 `main` 创建 `feat/cli-multiuser` 分支。
3. 实现新包结构、配置模板、`setup.py`、单元测试。
4. 删除旧脚本 `main.py`/`dingtalk.py`/`feishu.py`/`captcha_ocr.py` 与 `auto-commit.yml`。
5. 重写 `README.md` 为 CLI 用法。
6. 提交并推送至 `origin/feat/cli-multiuser`。
7. 不创建 PR（按用户要求"上传 GitHub 使用"，提供分支即可）。
8. `main` 分支保持原状，验证通过后由用户决定是否合并。

---

## 14. 假设与约束

- 教务系统 URL `http://zhjw.qfnu.edu.cn` 与登录流程不变；变更视为超出本期范围。
- `ddddocr` 在 Python 3.10–3.13 上可用（`setup.py` 已固化 `python_requires`）。
- 用户规模 < 50，串行执行可接受。
- 密码以环境变量引用形式存放，明文仅本地测试。
- 目标平台 Linux/macOS；Windows 仅支持 `Ctrl+C`，`SIGTERM` 不生效。
- 所有时间以本机系统时区为准。
- 环境变量在进程启动时一次性读取，修改后需重启进程。
