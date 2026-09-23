# 使用文档

本文档介绍 **service-reachable-monitor** 的安装、配置、运行与排障。
架构设计与脱敏说明见 [architecture.md](architecture.md)。

---

## 目录

1. [环境要求](#1-环境要求)
2. [安装](#2-安装)
3. [配置](#3-配置)
4. [运行](#4-运行)
5. [定时调度](#5-定时调度)
6. [通知渠道](#6-通知渠道)
7. [可选：MySQL 元数据库](#7-可选mysql-元数据库)
8. [作为库调用](#8-作为库调用)
9. [测试](#9-测试)
10. [排障 FAQ](#10-排障-faq)

---

## 1. 环境要求

| 项目 | 要求 |
| --- | --- |
| Python | 3.8+（开发验证于 3.12） |
| 核心依赖 | `requests`、`PyYAML` |
| Dubbo 检查 | 额外需要 `kazoo`，且运行机能访问目标 ZooKeeper |
| 定时调度 | 额外需要 `APScheduler` |
| 元数据库 | 额外需要 `PyMySQL` |

> 只跑 HTTP 巡检时，安装核心依赖即可，无需 `kazoo`。

---

## 2. 安装

### 方式 A：直接运行（推荐，免安装）

```bash
git clone https://github.com/luoxiyuan/service-reachable-monitor.git
cd service-reachable-monitor
pip install -r requirements.txt
python monitor.py --help
```

`monitor.py` 会自动把 `src/` 加入 `sys.path`，无需安装包。

### 方式 B：以包形式安装

```bash
pip install -e .                 # 开发模式，获得 service-monitor 命令
service-monitor --help
```

### 可选依赖

```bash
pip install -r requirements-optional.txt        # kazoo + PyMySQL + APScheduler + pytest
# 或按需
pip install "kazoo>=2.9"
```

---

## 3. 配置

工具由两份 YAML 驱动，**均提供 `.example` 模板，真实配置文件已被 `.gitignore` 忽略**：

| 文件 | 作用 | 模板 |
| --- | --- | --- |
| `config/targets.yaml` | 监控**什么**（环境、HTTP URL、Dubbo 注册中心） | `config/targets.example.yaml` |
| `config/notify.yaml` | 失败时**怎么报**（渠道、限流、批量） | `config/notify.example.yaml` |
| `.env` | 存放**密钥**（webhook token、数据库口令等） | `.env.example` |

```bash
cp config/targets.example.yaml config/targets.yaml
cp config/notify.example.yaml  config/notify.yaml
cp .env.example .env
```

### 3.1 变量插值 `${VAR}`

两份 YAML 中任意字符串都可以写 `${VAR_NAME}`，运行时会用环境变量（或 `.env`）替换。
**变量缺失时替换为空串**，因此未配置的渠道会自动变成"未启用"而不会报错。

```yaml
channels:
  - type: dingtalk
    webhook: ${DINGTALK_WEBHOOK}   # 真实 token 只存在于 .env / CI Secret
```

`.env` 文件为简单的 `KEY=VALUE` 格式，支持 `#` 注释与引号包裹；
**已存在的环境变量优先级高于 `.env`**，方便容器/流水线通过平台注入密钥。

### 3.2 targets.yaml 字段说明

```yaml
defaults:                     # 全局默认值，可被单个目标覆盖
  http:
    timeout: 10               # 单次请求超时（秒）
    retries: 1                # 判定 DOWN 之前的额外重试次数
    verify_tls: true          # 是否校验证书（自签环境可设 false）
    follow_redirects: true
    expect_status: [200]      # 视为健康的状态码集合
    method: GET
    headers: {}
  dubbo:
    timeout: 10               # ZooKeeper 会话超时（秒）
    probe_port: true          # 额外对 provider ip:port 做 TCP 连通性探测
    fail_if_no_provider: true # 有 consumer 但无 provider 时判 DOWN

environments:
  - name: env-a               # 必填，唯一标识，用于 -e 选择
    label: "Environment A"    # 可选，报表展示名
    ip: 10.0.0.11             # 可选，仅用于丰富报表
    enabled: true             # false 时默认跳过，除非传 --all

    http_targets:
      - name: gateway-api     # 可选，缺省用 url
        url: https://api.example.com/health    # 必填，缺失则整条忽略
        owner: 13800138000    # 可选，负责人手机号，用于 @ 提醒
        expect_status: [200, 204]
        expect_body: '"status":"UP"'   # 可选，响应体子串断言
        retries: 2

    dubbo:
      zk_hosts: zk1.example.com:2181,zk2.example.com:2181   # 必填，缺失则忽略整个 dubbo 块
      root_path: services_env_a                            # Dubbo 注册的根路径
      applications: [demo-order-server]                    # 仅关注这些应用（用于 owner 猜测）
      include_interfaces: []                               # 空 = 全部
      exclude_interfaces: ["*.internal.DebugService"]      # 支持 * 通配
      owners:                                              # 应用 → 负责人
        demo-order-server:
          desc: "Order service"
          phone: 13800138000
          name: alice
          email: alice@example.com
```

`owner` 支持三种写法：

| 写法 | 示例 | 解析结果 |
| --- | --- | --- |
| 纯手机号 | `owner: 13800138000` | `phone=13800138000` |
| 映射 | `owner: {name: alice, phone: 138, email: a@x.com}` | 三者皆有 |
| 兼容旧格式 `"姓名,手机号"` | `owner: "alice,13800138000"` | `name=alice, phone=13800138000` |

> 一个环境若既没有 `http_targets` 也没有可用的 `dubbo` 块，会被自动跳过（`doctor` 会提示）。

### 3.3 notify.yaml 字段说明

```yaml
title: "服务可达性告警"     # 可选，告警正文首行

rate_limit:
  max_per_minute: 20        # 每分钟最多发送条数（群机器人频控）
  batch_size: 5             # 每条消息合并多少个失败目标
  wait_seconds: 60          # 超出预算后的等待时间

channels:                   # 按顺序逐个发送；未配置/未启用的自动跳过
  - type: console
    enabled: true
  - type: dingtalk
    enabled: true
    webhook: ${DINGTALK_WEBHOOK}
    secret: ${DINGTALK_SECRET}   # 可选，"加签"模式
    at_all: false
    at_mobiles: []               # 额外固定 @ 的手机号（负责人手机号会自动加入）
  - type: wecom
    enabled: false
    webhook: ${WECOM_WEBHOOK}
  - type: feishu
    enabled: false
    webhook: ${FEISHU_WEBHOOK}
    secret: ${FEISHU_SECRET}
  - type: webhook
    enabled: false
    url: ${CUSTOM_WEBHOOK_URL}
    timeout: 10
    headers: {Authorization: "Bearer ${CUSTOM_TOKEN}"}

alarm_center:               # 可选，对接内部电话告警平台
  enabled: false
  url: ${ALARM_CENTER_URL}
  fallback_number: ${ALARM_CENTER_FALLBACK_NUMBER}
```

> **没有任何可用渠道时会自动回落到 `console`**，保证巡检结果不会静默丢失。

---

## 4. 运行

### 4.1 子命令

| 命令 | 说明 |
| --- | --- |
| `run` | 巡检一次并对失败项告警（省略子命令时的默认行为） |
| `schedule` | 按 cron 周期性执行 `run`（需 APScheduler） |
| `inventory` | 导出 Dubbo provider 元数据（interface/application/host/port）为 JSON，不告警 |
| `list-envs` | 列出配置中的所有环境及其启停状态 |
| `doctor` | 校验两份配置、检查依赖，打印问题清单 |

### 4.2 全局参数

| 参数 | 说明 |
| --- | --- |
| `-c, --config` | targets 配置路径（默认 `targets.yaml`，env `MONITOR_TARGETS`） |
| `-n, --notify-config` | notify 配置路径（默认 `notify.yaml`，env `MONITOR_NOTIFY`） |
| `--env-file` | 指定 `.env` 文件 |
| `-e, --env NAME` | 只跑指定环境，可重复；默认跑所有 `enabled: true` 的环境 |
| `-k, --kind` | 只跑 `http` 或 `dubbo`，可重复 |
| `--all` | 连 `enabled: false` 的环境一起跑 |
| `--no-alert` | 只打印结果，不发送任何通知 |
| `--json PATH` | 额外把完整报告写成 JSON |
| `--quiet` | 只打印失败项与统计摘要 |
| `-v, --verbose` | DEBUG 级日志 |
| `--log-file PATH` | 日志同时写入文件 |
| `--log-level` | 日志级别（默认 `INFO`） |

> 全局参数写在子命令**前或后都可以**：`monitor.py -c x.yaml run` 与 `monitor.py run -c x.yaml` 等价。
> 配置文件名支持相对路径，会依次在 `当前目录`、`./config`、`仓库 config/` 下查找。

### 4.3 使用示例

```bash
# 配置自检（上线前必做）
python monitor.py doctor

# 只巡检 env-a 的 HTTP 服务，不发通知
python monitor.py run -e env-a -k http --no-alert

# 巡检所有启用环境，失败发通知，同时落一份 JSON
python monitor.py run --json reports/$(date +%F).json

# 包含被禁用的环境，只打印失败项
python monitor.py run --all --quiet

# 导出 Dubbo 接口清单（用于核对负责人 / 初始化元数据库）
python monitor.py inventory -o inventory.json
```

### 4.4 输出与退出码

标准输出为定宽表格 + 统计摘要：

```
ST    KIND  ENV            TARGET       OWNER        MESSAGE
----  ----  -------------  -----------  -----------  ----------------------------
OK    http  Environment A  gateway-api  alice        200 in 45.3ms
DOWN  dubbo Environment A  com.x.Demo   13800138000  no provider while 2 consumer(s) wait

duration: 6.2s | total=2 up=1 down=1 error=0 skipped=0 unknown=0

1 target(s) need attention:
  * Environment A: com.x.Demo [DOWN] | com.x.Demo | no provider while 2 consumer(s) wait
```

| 退出码 | 含义 |
| --- | --- |
| `0` | 全部可达 |
| `1` | 至少一个目标 DOWN / ERROR（便于 CI、crontab 判定） |
| `2` | 配置错误或用法错误 |

状态语义：

| 状态 | 含义 | 是否告警 |
| --- | --- | --- |
| `UP` | 探测成功 | 否 |
| `DOWN` | 服务不可达（HTTP 非预期状态码 / 超时 / 连接失败；Dubbo 无 provider 但有 consumer，或 provider 端口不通） | **是** |
| `ERROR` | 探测本身出错（配置错误、ZooKeeper 连不上、检查器异常） | **是** |
| `UNKNOWN` | 无法判定（Dubbo 既无 provider 也无 consumer） | 否 |
| `SKIPPED` | 主动跳过 | 否 |

---

## 5. 定时调度

### 5.1 内置调度（APScheduler）

```bash
pip install APScheduler
python monitor.py schedule --cron "*/10 10,14,16,18 * * mon-fri"
```

- 启动时会**立即先跑一次**，然后进入 cron 循环（前台阻塞）。
- 不传 `--cron` 时使用默认值 `mon-fri 10,14,16,18 每 10 分钟`，与历史脚本的巡检窗口一致。
- 也可用环境变量：`MONITOR_CRON="*/10 10,14,16,18 * * mon-fri"`。

cron 表达式为 5 段：`分 时 日 月 周`。

后台运行：

```bash
nohup python monitor.py schedule --log-file logs/monitor.log > /dev/null 2>&1 &
```

### 5.2 使用系统 crontab（无需 APScheduler）

```cron
*/10 10,14,16,18 * * 1-5 cd /opt/srm && python monitor.py run --quiet >> logs/cron.log 2>&1
```

---

## 6. 通知渠道

### 6.1 渠道类型

| type | 说明 | 关键参数 |
| --- | --- | --- |
| `console` | 打印到标准输出，无需密钥，CI 友好 | — |
| `dingtalk` | 钉钉群机器人，支持"加签" | `webhook`、`secret`、`at_all`、`at_mobiles` |
| `wecom` | 企业微信群机器人 | `webhook`、`at_all`、`at_mobiles` |
| `feishu` | 飞书群机器人，支持签名校验 | `webhook`、`secret` |
| `webhook` | 通用 JSON POST，转发到自建告警平台 | `url`、`timeout`、`headers` |
| `alarm_center` | 外部电话/告警中心（配在 `alarm_center:` 段） | `url`、`fallback_number`、`level` |

### 6.2 发送行为

- **只对 `DOWN` / `ERROR` 告警**；全部 `UP` 时一条消息都不发。
- 失败项按 `batch_size` 合并，每条消息形如：

  ```
  服务可达性告警
  - Environment A: gateway-api [DOWN] | https://api.example.com/health | timeout after 10s
    owner: alice
  ```
- 失败目标的负责人手机号会自动收集并作为 @ 对象（去重）。
- 每发一条都会先过 `RateLimiter`；超出每分钟预算时自动等待，避免被机器人频控拒绝。
- 单个渠道发送失败只记 WARNING，不影响其余渠道。
- 正文超过约 3800 字符会被截断并标注 `...(truncated)`。

### 6.3 各机器人 payload 形态

钉钉：`{"msgtype":"text","text":{"content":...},"at":{"atMobiles":[...],"isAtAll":false}}`
企业微信：`{"msgtype":"text","text":{"content":...},"mentioned_mobile_list":[...]}`
飞书：`{"msg_type":"text","content":{"text":...}}`
通用 webhook：`{"text":...,"mobiles":[...]}`

---

## 7. 可选：MySQL 元数据库

原始脚本把"环境 → ZK 地址"、"接口 → 应用"、"应用 → 负责人"三张表放在 MySQL 里，
并用字符串拼接 SQL 读写。本工具**默认不需要数据库**（负责人直接写在 `targets.yaml`），
但如果你的接口数量很大、希望集中维护负责人，可以启用可选的元数据库。

### 7.1 建表

```bash
mysql -h <host> -u <user> -p <db> < sql/ddl.sql
```

`sql/ddl.sql` 创建三张表（均为脱敏后的通用结构）：

| 表 | 作用 |
| --- | --- |
| `monitor_environment` | 环境名 → ZK 地址 / 根路径 |
| `monitor_interface` | 接口 → 应用 / 端口 / 最近一次 provider IP |
| `monitor_application` | 应用 → 负责人姓名 / 手机 / 邮箱 / 是否告警（`send_flag`） |

### 7.2 连接配置

只通过环境变量注入，**不允许写在代码或提交到仓库**：

```bash
# .env
MYSQL_HOST=db.example.com
MYSQL_PORT=3306
MYSQL_USER=monitor
MYSQL_PASSWORD=******
MYSQL_DATABASE=monitor
```

```python
from service_monitor.store import MetadataStore, StoreConfig

store = MetadataStore(StoreConfig.from_env())
owner = store.owner_for_interface("com.example.DemoService")   # -> Owner(...)
store.upsert_inventory(json.load(open("inventory.json")))      # 刷新接口清单
```

要点：

- `pymysql` 懒加载，未安装时抛出带安装提示的 `StoreError`，不影响主流程；
- **全部使用参数化查询**（`%s` 占位），杜绝原脚本的 SQL 注入风险；
- `send_flag = 0` 的应用视为"静音"，`owner_for_interface` 返回 `None`。

---

## 8. 作为库调用

```python
from service_monitor.config import load_monitor_config
from service_monitor.notifiers.manager import load_notify_config, NotificationManager
from service_monitor.runner import MonitorRunner
from service_monitor.reporting import render_table

config = load_monitor_config("config/targets.yaml")
notify = NotificationManager(load_notify_config("config/notify.yaml"))

runner = MonitorRunner(config=config, notify=notify)
report = runner.run(environments=["env-a"], kinds=["http"])

print(render_table(report.results))
print(report.digest(), "failures:", len(report.failures))
```

单独使用某个 checker：

```python
from service_monitor.checkers import HttpChecker, DubboChecker

for result in HttpChecker().check_environment(env):
    print(result.status, result.target.name, result.message)

rows = DubboChecker().inventory(env.dubbo)   # provider 元数据清单
```

---

## 9. 测试

```bash
pip install pytest
python -m pytest tests -q
```

测试**不依赖真实网络、ZooKeeper 或 MySQL**：HTTP 用 fake session，Dubbo 用 fake ZK client，
数据库用 fake DB-API connection，因此 `kazoo` / `pymysql` 未安装也能全绿。

---

## 10. 排障 FAQ

| 现象 | 原因与处理 |
| --- | --- |
| `config error: targets config not found` | 未创建 `config/targets.yaml`，或 `-c` 路径写错。先 `cp config/targets.example.yaml config/targets.yaml` |
| `doctor` 报 `missing kazoo` | 配置里有 `dubbo` 块但未装 `kazoo`：`pip install kazoo` |
| Dubbo 结果全是 `ERROR`，提示连不上 ZooKeeper | 运行机到 ZK 的网络/白名单不通；先用 `zkCli.sh` 或 `nc -vz zk1 2181` 验证 |
| 大量接口是 `UNKNOWN` | 该接口在 ZK 下既无 provider 也无 consumer，通常是无用注册项，可用 `exclude_interfaces` 过滤 |
| `probe_port` 导致误报 DOWN | 注册中心里的 provider IP 是容器内网地址、运行机不可达。把 `dubbo.probe_port` 设为 `false`，只依据注册信息判定 |
| 钉钉返回 `keyword not in content` | 机器人配置了"自定义关键词"安全设置。把关键词加进 `notify.yaml` 的 `title`，或改用加签模式（配 `secret`） |
| 通知一条都没发 | 全部目标 `UP`；或渠道 `enabled: false` / webhook 为空（`${VAR}` 未在 `.env` 中赋值）。用 `doctor` 查看实际启用的 channels |
| 想临时静默某个应用 | 元数据库模式下把 `monitor_application.send_flag` 置 0；YAML 模式下删除对应 `owner` 或从 `applications` 中移除 |
| 自签证书导致 TLS error | 该目标设 `verify_tls: false`（仅测试环境） |
| CI 中想用退出码卡门禁 | `run` 在有失败时返回 1，可直接 `python monitor.py run --quiet --no-alert` |