# AGENTS.md

Haven（健健）— 个人高血压管理 Managed Deep Agent。基于
[managed-deepagents](https://github.com/langchain-ai/managed-deepagents) 构建。

---

## 开发路线

当前阶段使用 **MDA（Managed Deep Agents）** 快速开发与部署，快速验证产品形态。
后续将逐步用 **LangChain + LangGraph** 替代 MDA，自己掌控 agent 运行时、工具注入、
中间件链与部署流程，减少对 MDA 托管层的依赖。

迁移原则：

- 业务逻辑（`src/haven/` 下的 `tools/`、`application/`、`safety/`、`storage/`）保持纯函数/SQLAlchemy，
  不依赖 MDA 运行时，确保可直接迁移。
- 中间件链需改造为 LangGraph 原生中间件机制，保持执行顺序不变。
- `mda dev` / `mda build` / `mda deploy` 逐步替换为自建开发/构建/部署流程。

---

## 核心约束

- **单租户**：一个部署 = 一个人（[ADR-006](.docs/adr/ADR-006-单租户身份模型.md)）。
  本人 id 由 `HAVEN_OWNER_ID` 配置（默认 `owner`），**不从平台注入身份推导**。
  要服务另一个人 = 再部署一个实例（不同 `HAVEN_OWNER_ID` + 不同库）。
- **不做医疗诊断**：只记录与转达。血压分级、趋势、紧急判定全部由确定性函数计算，
  LLM 绝不自行判断。
- **无 `memory.py`**：MDA 记忆是部署级共享的，健康数据绝不写入。
  记忆注入 = `src/haven/middleware/memory_context.py`，确定性只读组装用户数据摘要，
  追加到 system prompt，不落库、不进线程历史。
- **无 `sandbox/`**：纯对话 + 确定性工具，不需要文件系统或 shell。

---

## 命令

```bash
uv sync --locked --extra dev    # 安装包与开发依赖
uv run python -m haven         # 独立 LangGraph 对话
uv run mda dev                 # MDA 本地开发
uv run mda build               # 构建部署产物
uv run mda deploy              # 部署到 LangSmith
uv run mda logs                # 查看部署日志
uv run mda delete              # 删除部署
uv run python -m unittest discover -s tests -v
uv run ruff check src tests agent.py identity.py tools
uv run pyright
uv build
```

离线验证使用 unittest、模拟模型和临时 SQLite；质量工具为 Ruff / Pyright。
平台评测可另用 `uv run mda evals`。

---

## 项目结构

```text
haven/
  .python-version      # 固定本地 Python 3.14
  README.md
  pyproject.toml       # 元数据、依赖、Hatchling 与 MDA Python 版本
  uv.lock
  agent.py             # MDA 工具/中间件/interrupt_on 装配入口
  identity.py          # MDA 准入认证
  instructions.md      # 提示词唯一编辑源，构建时加入 wheel
  tools/mcp.py         # MDA 原生 MCP 发现入口，不放业务工具
  src/haven/
    __init__.py        # 延迟导出公共会话接口
    __main__.py        # python -m haven
    cli.py             # 命令行入口
    session.py         # 独立父图与会话
    agents/            # 业务子图装配
    config/            # settings + 纯标准库 MCP/路径加载 + YAML/JSON
    storage/           # 异步 SQLAlchemy 与审计
    middleware/        # 急救、命令、安全、记忆、可观测性、同意、MCP
    tools/             # 11 个确定性业务工具
    application/       # 纯业务逻辑
    safety/            # 紧急词表/免责声明/输出过滤/降级
    messages/          # 消息与提示词组装
    reasoning/         # 显式 ReAct
    schemas/
  tests/               # 离线回归
  .docs/               # ADR、需求与设计
```

---

## 中间件执行顺序（不可变更）

1. `emergency_input` — 急救词优先于一切（LLM 前拦截）
2. `command` — `/hello` `/help` `/consent` `/cancel` 确定性路由
3. `output_safety` — 输出安全过滤 + 降级兜底
4. `memory_context` — 只追加 system prompt 内容块，不重写
5. `observability` — 工具耗时与去标识审计
6. `consent_policy` — 工具调用前的同意闸门
7. `mcp_policy` — 外部 MCP 白名单/审计/降级（只挂工具调用钩子）

---

## 技术栈

| 领域   | 选型                                                                    |
| ------ | ----------------------------------------------------------------------- |
| 运行时 | `managed-deepagents==0.10.1`                                             |
| 语言   | Python ≥3.14（本地与 MDA 部署目标均固定 3.14）                      |
| 包管理 | `uv`（`uv sync` / `uv.lock`）                                           |
| LLM    | `langchain==1.4.9` + `deepagents==0.7.34`（经 MDA 托管）                |
| 模型   | `langchain-deepseek`，`src/haven/config/model.yaml` 切换                      |
| 数据库 | SQLAlchemy 异步 + SQLite（dev）→ PostgreSQL（prod），首笔工具调用懒建表 |
| 配置   | `pydantic-settings`，`.env` 可调项唯一入口                              |

---

## 编码规范

- 注释、文档字符串、面向用户文案：**中文**。变量/函数/类名：英文。
- 工具函数用 `Annotated` + `Field(description=…)` 声明参数。
- 工具返回固定文案，模型只负责转达。数据库异常捕获 `DatabaseUnavailable`，返回降级文案。
- 使用 `from __future__ import annotations` + Pydantic v2 + SQLAlchemy 2.0 `Mapped`。
- **绝不**在日志中输出健康数据明文；**绝不**硬编码密钥（走 `.env`）。
- 紧急词表、血压阈值、免责声明是**产品语义**，不允许被环境变量改写。
- 设计功能或修改逻辑时，先思考整体方案、影响范围与边界条件，然后再动手实现。
- 每个关键路径都要有**保底方案**：工具调用失败、模型输出异常、外部依赖不可用时，
  必须有降级兜底逻辑（降级文案 / 默认值 / 安全退出），不允许静默失败或裸抛异常。

---

## 依赖版本约束

关键运行依赖在 `pyproject.toml` 精确固定，完整解析结果在 `uv.lock`。

- 当前组合：MDA 0.10.1、LangChain 1.4.9、Core 1.7.6、LangGraph 1.2.20、Deep Agents 0.7.34。
- 旧 MDA 0.6.1 曾存在工具 `runtime` 代理的 Pydantic 校验问题；升级后使用继承原生
  `ToolRuntime` 的新运行时，不再依赖旧私有 `_managed_runtime` 字段。
- `requires-python` 保持 `>=3.14`；`.python-version` 与 `[tool.mda].python-version`
  均为 `3.14`，不修改生成的 `langgraph.json` 来绕过兼容性检查。
- `asyncpg>=0.31.0`：旧版缺少 cp314 轮子。
- 普通 `import haven` 不加载健康数据或模型；`haven.config` 初始化保持为空。
- 源码 `.env` 使用项目根路径，wheel 安装使用启动目录；进程环境优先。
- MDA 0.10 会自动发布 `haven--general-purpose` 图；必须显式复用主入口的
  全部中间件与原顺序，不能让独立图绕过安全或同意闸门。

---

## 部署要点

- `.env` 需 `DEEPSEEK_API_KEY`。代码不直接读取，由 langchain-deepseek 从进程环境读取。
  `mda deploy` 将 `.env` 非保留变量转发为部署环境变量。
- **`HAVEN_OWNER_ID`**：本机 `mda dev` 与云端部署必须是同一个值，否则看到两份数据。
- **数据库**：部署前必须在 `.env` 设置 PostgreSQL URL（`postgresql+asyncpg://...`）。
  SQLite 仅限本地 dev，不要带去部署。
- MDA 的 `interrupt_on` 由管理运行时处理；独立入口用原生 `interrupt()` 和进程内检查点。
  两条路径都必须明确批准，独立进程退出后不能恢复旧审批。
- 工具内建 SQL 去标识审计（`audit_logs.subject_key`），删除数据时仅保留审计事件本身。

---

## 外部 MCP

- 声明在根目录 `tools/mcp.py`，使用 MDA 0.10 的 `define_mcp(servers=...)`。
- 配置在 `src/haven/config/mcp.json`，`${VAR}` 从进程环境 → `.env` 解析。
- 仅支持远程 HTTP/SSE；空/缺失或白名单为空则关闭。
- 双保险：`include_tools` 白名单 + `mcp_policy` 默认拒绝、审计和失败降级。
- 工具名格式 `{服务器名}_{远端工具名}`，MDA 0.10 不再支持旧双下划线选项。
- `haven` 和 `haven.config` 初始化保持轻量，MCP 配置和路径加载仅依赖标准库；
  MDA 声明只导入配置加载器与平台接口，不引入模型或数据库。
- 提取环境尚未安装 Haven 包，根目录声明通过 `run_path` 加载同一份标准库配置实现；
  不修改 `sys.path`，不维护第二份清单解析逻辑。

---

## 相关文档

| 文档                                       | 用途                             |
| ------------------------------------------ | -------------------------------- |
| [instructions.md](instructions.md)         | 健健系统提示（行为规范完整脚本） |
| [agent.py](agent.py)                       | Agent 装配入口                   |
| [.docs/adr/](.docs/adr/)                   | 架构决策记录（ADR-001 ~ 008）    |
| [.docs/requirements/](.docs/requirements/) | 需求文档（SPRINT-0.0.1 ~ 0.0.3） |
