# Haven（健健）

个人高血压管理智能体：记录与转达健康数据，不做医疗诊断。支持独立 LangGraph
命令行与 MDA 托管入口；一个部署只服务一个人。

## 开发环境

使用 Python 3.14（`.python-version`）与 uv：

```bash
uv sync --locked --extra dev
uv run python -m haven
uv run haven "您好"
```

将 `.env.example` 复制为 `.env`，自行填写模型密钥及数据库地址。
源码或 editable 安装从项目根读取 `.env`；wheel 安装从启动目录读取 `.env`。
进程环境优先。SQLite 相对路径仍相对于启动目录，部署应配置 PostgreSQL。
模型与 MCP 配置位于 `src/haven/config/model.yaml`、`src/haven/config/mcp.json`。

旧命令 `uv run python haven.py` 已由上面的包入口替代。
交互模式支持 `/approve`、`/reject` 和 `/exit`，详见
[独立入口说明](.docs/standalone.md)。

## 目录与接口

- `src/haven/`：业务包，工具、中间件、存储与独立会话。
- `agent.py`、`identity.py`：MDA 装配与身份声明。
- `tools/mcp.py`：MDA 0.10 的 MCP 发现入口；业务工具在包内。
- `instructions.md`：提示词唯一编辑源，构建时加入 wheel 资源。
- `tests/`：模拟模型及临时数据库的离线回归。

```python
from haven import HavenSession, SessionReply, create_haven, run_once
```

普通 `import haven` 不创建模型、不读取提示词、不连接数据库。

## 验证与构建

```bash
uv run python -m unittest discover -s tests -v
uv run ruff check src tests agent.py identity.py tools
uv run pyright
uv build
```

MDA CLI 使用项目锁定的 0.10.1，部署目标由 `[tool.mda]` 固定为 Python 3.14：

```bash
uv run mda build
uv run mda dev --no-browser --no-reload
```

MCP 使用 `define_mcp`，工具名为 `{server}_{tool}`；白名单为空则服务器不启用，
工具调用仍受策略中间件的审计和降级约束。健康数据不写入 MDA 共享记忆。
MDA 自动发布的 `haven--general-purpose` 图也显式复用主入口的安全中间件链。
本次结构迁移不包含云端部署、HTTP 服务或持久会话检查点。
