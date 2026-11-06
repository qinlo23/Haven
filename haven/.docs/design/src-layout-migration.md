# src 布局迁移与 MDA 升级验收

日期：2026-11-06。Python：3.14.4。

## 实施结果

业务代码迁入 `src/haven/`，保留根目录 MDA 装配、身份和提示词入口。
独立图与会话由 `haven.session` 提供，CLI 为 `haven.cli`，普通 `import haven`
延迟加载公共接口。业务阈值、数据库结构、审批及既有中间件执行顺序保持不变。

入口命令：

```bash
uv sync --locked --extra dev
uv run python -m haven
uv run haven "您好"
uv run mda build
uv run mda dev --no-browser --no-reload
```

旧 `python haven.py` 命令不再保留。全局 `mda` 和项目内 CLI 均升级至 0.10.1；
建议开发时使用 `uv run mda`，确保运行项目锁定版本。

## 运行依赖与平台兼容

| 项目 | 验证版本 |
| --- | --- |
| managed-deepagents | 0.10.1 |
| langchain（含 mcp extra） | 1.4.9 |
| langchain-core | 1.7.6 |
| langgraph | 1.2.20 |
| deepagents | 0.7.34 |
| mcp | 2.3.0 |
| langchain-deepseek | 1.1.1（未升级） |
| SQLAlchemy | 2.0.52（未升级） |

MDA 0.6.1 固定生成 Python 3.11 的问题已通过升级解决；`.python-version`
和 `[tool.mda].python-version` 均固定为 3.14。最终构建生成 Python 3.14 配置，
临时开发服务的解释器实测为 3.14.4。

必要的依赖变化：新版 MDA 开发服务需要 `langchain[mcp]` 和 MCP 2.x，旧的
`langchain-mcp-adapters` 仅兼容 MCP 1.x，且项目代码没有使用，因此移除。
`certifi` 随 Core 的新最低要求升级；其他变化由新版运行栈依赖解析产生，
完整版本在 `uv.lock`。

新版 `ManagedToolRuntime` 继承原生 `ToolRuntime`，移除依赖旧私有代理字段的解包。
身份否决从可信的 `server_info.principal` 读取，仍以 `HAVEN_OWNER_ID` 授予本人身份。

## 配置、资源与 MDA 适配

- 根目录 `instructions.md` 是唯一编辑源，Hatchling 在 wheel 中加入
  `haven/resources/instructions.md`；源码读原文件，安装包读资源，缺失或空内容拒绝装配。
- YAML/JSON 随 `haven.config` 打包；源码 `.env` 使用项目根路径，wheel 使用启动目录，
  进程环境优先。SQLite 相对路径仍以启动目录为准。
- MDA 0.10 的发现入口为根目录 `tools/mcp.py`，业务工具均在 `haven.tools`。
  平台提取环境尚未安装 Haven，入口通过标准库 `run_path` 加载同一份
  `src/haven/config/mcp.py`，不修改 `sys.path`，不复制配置解析逻辑。
- 新 MCP 工具名为 `{server}_{tool}`，同步更新提示词和白名单匹配。
  保留空白名单关闭、审计、默认拒绝和远端失败降级；旧平台参数不传给新声明。
- MDA 会把 SDK 改为本地 vendored runtime 引用，Hatchling 已显式允许该直接引用。
- 新版自动发布的 `haven--general-purpose` 图默认不继承中间件，因此显式配置为
  复用主入口完整安全链；没有新增自定义业务分工。

## 验证结果

| 验证 | 结果 |
| --- | --- |
| 升级前离线基线 | 83 个测试通过 |
| 升级后、迁移前回归 | 83 个测试通过 |
| 最终完整离线回归 | 93 个测试通过 |
| MDA 原生工具运行时注入 | 实际编译托管图并调用工具通过，runtime 不暴露到工具 schema |
| 自动生成助手图安全链 | 实际编译该图，确定性命令不调用模型 |
| Ruff | 通过 |
| Pyright | 0 错误，1 个既有 PyYAML `reportMissingModuleSource` 提示 |
| 锁文件一致性与差异空白检查 | 通过 |
| wheel 与源码分发包构建 | 通过，wheel 从源码分发包构建 |
| 分发包内容 | 包含提示词、模型 YAML、MCP JSON，不含 `.env` |
| 独立 Python 3.14 wheel 环境 | 从仓库外导入公共接口、轻量导入、资源和 `.env` 优先级通过 |
| CLI | `haven --help`、`python -m haven --help` 通过；既有交互/单次/审批回归通过 |
| MDA 构建 | Python 3.14，主图与自动生成图均存在 |
| MDA 本地服务 | 临时副本启动成功，健康检查、助手列表、主图和自动生成图的 `/hello`、内存数据库 `/consent` 均返回 HTTP 200 |

沙箱会使原有异步测试停滞，全部正式回归在获准的沙箱外运行，使用模拟模型与临时
数据库。MDA 启动验证使用临时源码副本、占位模型密钥和内存数据库；没有调用真实
DeepSeek 模型，没有操作真实健康数据，也没有执行云端部署。

构建产物与验证日志位于 `/tmp`，不作为仓库源码提交。历史阻塞复现见
[升级前检查记录](src-layout-migration-preflight.md)。
临时 MDA 服务验证完成后已停止。
