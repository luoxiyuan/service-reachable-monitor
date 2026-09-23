# 架构与脱敏说明

本文档说明 service-reachable-monitor 的由来、架构设计，以及从历史脚本到本仓库的**脱敏对照**。

---

## 1. 背景：历史脚本梳理

本工具源自一组分散的环境监控脚本，主要包含：

| 原始脚本 | 职责 | 主要问题 |
| --- | --- | --- |
| `env_monitor.py` | Dubbo 服务巡检（ZooKeeper + MySQL 环境表 + 钉钉/告警中心） | 数据库账号、webhook token、手机号硬编码；SQL 字符串拼接；逻辑与配置耦合 |
| `env_monitor_http.py` | HTTP 服务巡检（每环境一份 URL 字典 + 钉钉） | 每套环境的 URL、负责人手机号全部写死在源码里 |
| `monitor-dubbo.py` | Dubbo 巡检变体（钉钉 + 企业微信 + 飞书） | 同上，且三个机器人 token 硬编码 |
| `monitor_http.py` | HTTP 巡检变体 | 同上 |
| `monitor-dsf.sh` | DSF 集群状态检查 + 飞书通知 | 飞书 webhook 硬编码在 shell 里 |
| `config.ini` / `tools/*` | 数据库、Git 凭据与读取工具 | 明文凭据入库 |

**共性痛点**：

1. 敏感信息（IP、手机号、DB 账号、webhook token、内部域名）直接写死在源码；
2. "监控什么"与"怎么监控"耦合，新增环境要改代码；
3. SQL 使用 `%` 字符串拼接，存在注入风险；
4. 无测试、无统一日志、通知逻辑各写一份；
5. 多份脚本重复实现同一套 Dubbo/HTTP 判定逻辑，口径不一致。

---

## 2. 重构目标

- **抽取**：把 HTTP 与 Dubbo 两类可达性判定的核心逻辑提炼为独立、可测试的 checker；
- **解耦**：监控目标、通知渠道、密钥全部外置为配置 / 环境变量；
- **脱敏**：仓库内不出现任何真实 IP、手机号、token、域名、账号；
- **工程化**：统一数据模型、报表、日志、CLI、调度，补齐单元测试；
- **可复用**：换一套 `targets.yaml` 即可监控任意环境，无需改代码。

---

## 3. 总体架构

```
                         ┌────────────────────────────┐
   targets.yaml ───────► │        config.py           │  ${VAR} 插值 + .env
   notify.yaml  ───────► │  (MonitorConfig / Notify)  │
                         └──────────────┬─────────────┘
                                        ▼
                         ┌────────────────────────────┐
                         │         runner.py          │  选环境 → 选 checker → 汇总
                         └──────┬──────────────┬──────┘
                                ▼              ▼
                     ┌───────────────┐  ┌────────────────┐
                     │ HttpChecker   │  │ DubboChecker   │
                     │ (requests)    │  │ (kazoo / ZK)   │
                     └───────┬───────┘  └───────┬────────┘
                             ▼                  ▼
                     ┌────────────────────────────────────┐
                     │  models: CheckResult / RunReport   │
                     └───────────────┬────────────────────┘
                                     ▼
                     ┌────────────────────────────────────┐
                     │  notifiers/manager (限流 + 批量)   │
                     │  console / dingtalk / wecom /      │
                     │  feishu / webhook / alarm_center   │
                     └───────────────┬────────────────────┘
                                     ▼
                            reporting.py（表格 / JSON / 日志）
```

分层职责：

| 层 | 模块 | 职责 |
| --- | --- | --- |
| 模型 | `models.py` | `Status` / `Owner` / `Target` / `CheckResult` / `RunReport`，全流程共用 |
| 配置 | `config.py` | 读取 YAML、`${VAR}` 插值、`.env` 加载、类型化配置对象 |
| 探测 | `checkers/` | 一类服务一个 checker，输入环境配置，输出 `CheckResult` 列表，**单目标出错不抛异常** |
| 编排 | `runner.py` | 选环境、选 checker、聚合结果、触发告警 |
| 通知 | `notifiers/` | 各渠道实现 + `NotificationManager`（编排）+ `RateLimiter`（限流） |
| 报表 | `reporting.py` | 表格渲染、JSON 导出、日志配置 |
| 调度 | `scheduler.py` | cron 解析 + APScheduler 封装 |
| 存储 | `store.py` | 可选 MySQL 元数据（负责人 / 接口清单） |
| 入口 | `cli.py` / `__main__.py` / `monitor.py` | 命令行与免安装入口 |

---

## 4. 核心逻辑保留与改进

### 4.1 Dubbo 可达性判定（沿用原逻辑）

ZooKeeper 根路径（如 `/services_env_a`）下，Dubbo 为每个接口建一个节点，节点下有 `providers` 与 `consumers` 两类子节点。判定口径与原脚本一致：

| providers | consumers | 结论 | 说明 |
| --- | --- | --- | --- |
| 非空 | 任意 | `UP` | 有服务在提供 |
| 空 | 非空 | `DOWN` | **有人要用但没人提供 → 服务掉了，值得告警** |
| 空 | 空 | `UNKNOWN` | 无人注册，不可操作 |

改进点：

- 原脚本解析 provider URL（`dubbo://host:port/interface?application=...&methods=...`）用于"接口入库"，
  这里保留为 `parse_provider_url()`，并对**畸形节点降级**为 `{"raw": ...}`，单个坏节点不会中断整轮扫描；
- 新增可选 `probe_port`：对 provider 的 `ip:port` 做 TCP 连通性探测，识别"注册在、进程死"的情况；
- 负责人解析：原脚本走 MySQL join，这里默认从 provider URL 的 `application` 字段映射 `targets.yaml` 的 `owners`，
  **无需数据库**；MySQL 路径作为可选能力保留在 `store.py`。

### 4.2 HTTP 可达性判定

- 对每个 URL 真实发起请求，`timeout` 默认 10s（与原脚本一致）；
- 状态码在 `expect_status`（默认 `[200]`）内视为健康，否则 `DOWN`；
- 新增 `expect_body` 子串断言、`retries` 重试、`method` / `headers` 自定义；
- 异常分类：超时 / 连接错误 / TLS 错误 → `DOWN`；其他请求异常 → `ERROR`。

### 4.3 限流与批量（沿用原逻辑）

原脚本注释写明"钉钉每分钟最多约 20 条，每 5 条合并，超限 sleep 60s"。
这里用 `RateLimiter`（滑动窗口）+ `batch_size` 显式实现，数值可在 `notify.yaml` 调整。

### 4.4 调度（沿用原逻辑）

原脚本 APScheduler 配置为 `day_of_week='mon-fri', hour='10,14,16,18', minute='0/10'`，
这里作为 `scheduler.DEFAULT_CRON` 默认值保留，并支持 `--cron` / `MONITOR_CRON` 覆盖。

---

## 5. 脱敏对照

> 原则：仓库内**任何文件**都不得出现真实生产信息。下表为类别级对照（不列出真实值本身）。

| 类别 | 原始脚本中的形态 | 本仓库的处理 |
| --- | --- | --- |
| 内网 IP | 真实 `10.37.x.x` / `10.40.x.x` 段 | 占位 `10.0.0.x`，或仅作为报表字段示例 |
| 内部域名 | 多个真实企业内网/测试域名 | 统一替换为 `example.com` / `example-im.com` |
| 手机号 | 真实负责人手机号（多个） | 示例值 `13800138000` / `139...`，真实值走 `targets.yaml`（git 忽略）或 `.env` |
| 邮箱 / 作者 | 真实企业邮箱、作者署名 | 移除；示例用 `alice@example.com` |
| MySQL | 真实 host / 账号 / 口令（含 `config.ini`） | 全部改为 `MYSQL_*` 环境变量；`sql/ddl.sql` 为脱敏通用建表语句 |
| 钉钉 token | 多个真实 `access_token=...` | `${DINGTALK_WEBHOOK}`，样例值 `REPLACE_ME` |
| 企业微信 key | 真实 `key=...` | `${WECOM_WEBHOOK}`，样例 `REPLACE_ME` |
| 飞书 webhook | 真实 hook UUID | `${FEISHU_WEBHOOK}`，域名改 `open.example-im.com` |
| 告警中心 | 内部告警平台 URL / 接口约定 | `${ALARM_CENTER_URL}`，仅保留通用 JSON POST 契约 |
| 集群/应用名 | 真实业务前缀命名的应用与集群 | 示例用 `demo-order-server` / `demo-user-server` |
| 内部文档链接 | confluence 页面 URL | 移除 |
| SQL | `%` 字符串拼接（注入风险） | `store.py` 全部参数化查询 |

### 5.1 防泄漏机制

- `.gitignore` 忽略 `.env`、`config/targets.yaml`、`config/notify.yaml`、`logs/`；
- 仓库只提交 `*.example` 模板，模板内一律为占位符 / `REPLACE_ME`；
- 渠道实现里对含 `REPLACE_ME` 的 webhook 直接判定为"未启用"，避免误发到占位地址；
- 提交前建议执行自检（见下）。

### 5.2 提交前脱敏自检

```bash
# 确认没有真实密钥/内网信息被带入（应无输出）
# 把 <内网域名>/<真实网段> 换成你方需要拦截的具体特征
grep -rnE "access_token=[A-Za-z0-9]{20,}|<内网域名>|<真实网段>" \
     --include="*.py" --include="*.yaml" --include="*.md" --include="*.sql" .

# 确认 .env 与真实配置未被纳入版本控制
git status --porcelain | grep -E "\.env$|config/(targets|notify)\.yaml$" || echo "clean"
```

---

## 6. 设计取舍

| 取舍 | 说明 |
| --- | --- |
| 配置驱动 vs 数据库驱动 | 默认 YAML（零依赖、易审查、可 diff）；MySQL 作为**可选**扩展，适配接口量极大的场景 |
| `src/` 布局 | 避免测试误 import 到未安装的本地包，`conftest.py` 显式注入 `src` |
| checker 不抛异常 | 单目标失败转为 `ERROR` 结果，保证一轮巡检不会因个别坏目标中断 |
| 渠道降级 | 未配置/占位 token 的渠道自动跳过；全部不可用时回落 console，结果绝不静默 |
| 懒加载可选依赖 | `kazoo` / `pymysql` / `apscheduler` 仅在使用对应能力时 import，核心 HTTP 巡检零额外依赖 |
| 测试全用 fake | 不依赖真实网络/ZK/DB，CI 可在最小环境跑全绿 |

---

## 7. 与原脚本的能力对照

| 能力 | 原脚本 | 本仓库 |
| --- | --- | --- |
| HTTP 巡检 | ✅ 硬编码 URL 表 | ✅ `targets.yaml` 配置 |
| Dubbo 巡检 | ✅ providers/consumers 判定 | ✅ 同口径 + TCP 探测 + 过滤 |
| 接口清单入库 | ✅ MySQL 拼接 SQL | ✅ `inventory` 命令 + 参数化 `store.py`（可选） |
| 负责人 @ 提醒 | ✅ 手机号硬编码 | ✅ owner 配置 / 元数据库 |
| 钉钉/企业微信/飞书 | ✅ token 硬编码 | ✅ `${VAR}` 注入 + 加签支持 |
| 告警中心电话 | ✅ 内部接口 | ✅ 通用 `alarm_center` webhook |
| 限流批量 | ✅ 写死 20/min, batch 5 | ✅ 可配置 |
| 定时调度 | ✅ APScheduler 写死 cron | ✅ `schedule` + `--cron` |
| 多环境 | ⚠️ 每环境一份函数/字典 | ✅ 统一 `environments[]` |
| 单元测试 | ❌ | ✅ 110+ |
| 统一日志/报表 | ⚠️ print + saveLog | ✅ logging + 表格/JSON |