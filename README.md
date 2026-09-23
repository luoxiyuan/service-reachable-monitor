# service-reachable-monitor

> 面向多测试环境的 **HTTP 服务** 与 **Dubbo 服务** 可达性巡检工具。
> 配置驱动、密钥外置、渠道可插拔，巡检失败时按负责人 @ 提醒。

本仓库是从一组历史"环境监控脚本"中**抽取核心逻辑 + 全面脱敏 + 工程化重构**后的成果：
把散落在多个脚本里、硬编码了 IP / 手机号 / 数据库账号 / 群机器人 token 的监控逻辑，
整理成一个结构清晰、可配置、可测试、可复用的 Python 包。

---

## 功能特性

- **HTTP(S) 可达性探测**：真实发起请求，支持超时、重试、`expect_status`、`expect_body` 断言。
- **Dubbo 服务探测**：基于 ZooKeeper 注册中心，沿用原始脚本的判定逻辑——
  *有 provider → UP；无 provider 但有 consumer → DOWN（服务掉了）；两者皆无 → UNKNOWN*，
  并可选对 provider 的 `ip:port` 做 TCP 连通性探测。
- **多环境**：一份配置管理 stable / betaa / betab … 等多套环境，可单独选跑或整体禁用。
- **可插拔通知渠道**：控制台、钉钉、企业微信、飞书、通用 Webhook、外部告警中心（电话）。
- **限流与批量**：内置滑动窗口限流（默认每分钟 20 条）+ 失败批量合并（默认每批 5 条），
  避免触发群机器人频控。
- **密钥外置**：所有 webhook token、数据库口令、手机号均通过 `${VAR}` 环境变量 / `.env` 注入，
  **源码与配置样例中不含任何真实密钥**。
- **定时调度**：内置 cron 调度（默认 `mon-fri 10,14,16,18 每 10 分钟`），等价于原脚本的 APScheduler 配置。
- **可选元数据库**：MySQL 存储"接口 → 应用 → 负责人"清单（参数化查询，杜绝 SQL 注入）。
- **完善测试**：110+ 单元测试，全部使用 fake session / fake ZK / fake DB，无需真实网络。

---

## 目录结构

```
.
├── monitor.py                     # 免安装入口：python monitor.py run ...
├── pyproject.toml                 # 打包与依赖声明（可 pip install -e .）
├── requirements.txt               # 核心依赖：requests + PyYAML
├── requirements-optional.txt      # 可选依赖：kazoo / PyMySQL / APScheduler / pytest
├── .env.example                   # 密钥模板（复制为 .env，已被 git 忽略）
├── config/
│   ├── targets.example.yaml       # 监控目标样例（复制为 targets.yaml）
│   └── notify.example.yaml        # 通知渠道样例（复制为 notify.yaml）
├── sql/
│   └── ddl.sql                    # 可选元数据库建表语句（脱敏）
├── src/service_monitor/
│   ├── models.py                  # 数据模型：Status / Owner / Target / CheckResult / RunReport
│   ├── config.py                  # YAML 加载 + ${VAR} 插值 + 类型化配置
│   ├── checkers/
│   │   ├── http_checker.py        # HTTP 探测
│   │   └── dubbo_checker.py       # Dubbo / ZooKeeper 探测
│   ├── notifiers/
│   │   ├── console.py / chat.py / webhook.py / alarm.py
│   │   └── manager.py             # 渠道编排 + 限流 + 批量
│   ├── store.py                   # 可选 MySQL 元数据存取
│   ├── reporting.py               # 报表渲染 / JSON 导出 / 日志
│   ├── runner.py                  # 巡检编排
│   ├── scheduler.py               # cron 调度
│   └── cli.py                     # 命令行入口
├── tests/                         # pytest 测试套件
└── docs/
    ├── usage.md                   # 详细使用文档
    └── architecture.md            # 架构与脱敏说明
```

---

## 快速开始

```bash
# 1) 安装依赖（核心）
pip install -r requirements.txt
# 需要 Dubbo 检查 / 定时调度 / 元数据库时再装可选项
pip install -r requirements-optional.txt

# 2) 准备配置
cp config/targets.example.yaml config/targets.yaml   # 编辑成你的环境
cp config/notify.example.yaml  config/notify.yaml    # 编辑通知渠道
cp .env.example .env                                 # 填入真实密钥（不会提交）

# 3) 自检配置
python monitor.py doctor

# 4) 跑一次巡检（只看 HTTP，不发通知）
python monitor.py run -k http --no-alert

# 5) 正式跑（按 targets.yaml 选定的环境，失败自动告警）
python monitor.py run -e env-a
```

> 也可以先安装包再用 `service-monitor` 命令：`pip install -e .` 然后 `service-monitor doctor`。

---

## 常用命令

| 命令 | 说明 |
| --- | --- |
| `run` | 巡检一次并对失败项告警（默认子命令） |
| `schedule` | 按 cron 周期性巡检（需 APScheduler） |
| `inventory` | 导出 Dubbo provider 元数据为 JSON（不告警） |
| `list-envs` | 列出已配置的环境 |
| `doctor` | 校验配置文件与依赖，报告问题 |

常用全局参数：`-c/--config`、`-n/--notify-config`、`-e/--env`（可重复）、
`-k/--kind http|dubbo`（可重复）、`--all`（含被禁用环境）、`--no-alert`、
`--json PATH`、`--quiet`、`-v`。

退出码：`0` 全部可达 / `1` 存在失败 / `2` 配置或用法错误。

---

## 配置示例

`config/targets.yaml`（节选）：

```yaml
environments:
  - name: env-a
    label: "Environment A"
    http_targets:
      - name: gateway-api
        url: https://api.example.com/health
        owner: 13800138000          # 失败时 @ 该手机号
        expect_status: [200]
        expect_body: '"status":"UP"'
    dubbo:
      zk_hosts: zk1.example.com:2181,zk2.example.com:2181
      root_path: services_env_a
      applications: [demo-order-server]
      exclude_interfaces: ["*.internal.DebugService"]
      owners:
        demo-order-server: { desc: "Order service", phone: 13800138000 }
```

`config/notify.yaml`（节选）：

```yaml
rate_limit: { max_per_minute: 20, batch_size: 5, wait_seconds: 60 }
channels:
  - { type: console, enabled: true }
  - type: dingtalk
    enabled: true
    webhook: ${DINGTALK_WEBHOOK}     # 真实 token 放在 .env
    secret:  ${DINGTALK_SECRET}
```

完整字段说明见 [docs/usage.md](docs/usage.md)，架构与脱敏说明见 [docs/architecture.md](docs/architecture.md)。

---

## 安全与脱敏

本仓库由历史脚本重构而来，已做如下脱敏处理，**不包含任何真实生产信息**：

- 真实 IP / 域名 / 集群名 → 替换为 `example.com`、`10.0.0.x` 等占位符；
- 手机号 / 邮箱 / 作者信息 → 替换为 `13800138000` 等示例值；
- 数据库地址 / 账号 / 口令 → 改为环境变量注入，`sql/ddl.sql` 为脱敏建表语句；
- 钉钉 / 企业微信 / 飞书 webhook token → 改为 `${VAR}` 引用，样例中为 `REPLACE_ME`。

详见 [docs/architecture.md](docs/architecture.md) 的"脱敏对照"章节。

## License

MIT — 见 [LICENSE](LICENSE)。