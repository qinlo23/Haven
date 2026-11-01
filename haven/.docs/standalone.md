# Haven 独立入口：显式 StateGraph

模块与适配设计见 [ReAct 首版设计](design/react-v1.md)，四步迁移已接入当前入口。

现有 `mda dev` / `mda deploy` 继续使用原有入口。`haven.py` 通过显式 StateGraph
装配项目 Agent，挂载高血压业务子图，复用 11 个业务工具和原有中间件，无需
MDA 编译；独立路径不再使用 `create_agent`。

```text
haven.py：急救拦截 → 命令路由 → 高血压业务子图
agents/hypertension.py：业务工具、提示、中间件和审批规则
reasoning/react.py：思考 → 审批 → 行动并收集观察结果 → 再次思考
```

```bash
# 多轮对话与人工确认
uv run python haven.py
# 兼容单次对话
uv run python haven.py "您好"
```

CLI 从项目根目录的 `.env` 加载模型密钥和业务配置，进程环境优先，模型参数
沿用 `config/model.yaml`。实际写入沿用 `DATABASE_URL` 和 `HAVEN_OWNER_ID`；
试运行前请通过环境变量指定独立的开发数据库，避免操作原有健康数据：

```bash
DATABASE_URL=sqlite+aiosqlite:///./.data/haven-standalone.db uv run python haven.py
```

无参数启动后进入多轮对话。同一会话复用固定线程 ID 和进程内 `InMemorySaver`
检查点，模型可读取该会话前面的消息。输入 `/exit`、Ctrl+C 或结束输入退出。

异常血压确认和数据删除在 ReAct 的审批节点使用原生 `interrupt()` 暂停，终端显示
本批待确认工具及参数。输入 `/approve` 批准本批全部操作，或 `/reject` 拒绝。
不会自动批准，也不支持编辑工具参数；原有业务校验和过期检查仍在工具内执行。
存在敏感工具时整批先等待审批，之后按原顺序逐个处理；拒绝敏感项不取消本批
普通工具。每个工具结果与执行位置形成独立检查点，恢复不会重新执行此前已
完成的工具。数据库提交和检查点并非同一事务，故障后的未知写入仍需核实。

急救和 `/hello`、`/help`、`/consent`、`/cancel` 等确定性路由先于业务子图，
命中后不调用模型。每条新请求最多尝试调用模型 8 次，批准恢复不重置预算，
下一条新请求才会重新计数；工具失败不由执行器自动重试。

待确认时普通消息不会传给模型或恢复运行；`/cancel` 也不会代替拒绝，需先
`/reject`，再发送 `/cancel` 清理业务草稿或待确认血压。紧急消息仍优先返回
固定急救或澄清文案，不执行待确认操作；待确认状态保留，可继续拒绝或退出。

退出后，对话历史与未完成的运行中断丢失；健康记录、草稿、待确认血压等业务
数据仍保留在配置的数据库。重新启动需重新发起确认，无法批准上一进程的操作。
两条入口的线程不互通，避免同时确认或取消同一条业务记录。

带消息参数的单次模式在遇到敏感工具时只显示待确认操作并退出，不执行工具；
需要人工确认时请使用无参数的交互模式重新发起请求。

程序调用推荐使用 `HavenSession(model=...)`，多次 `await session.send(text)`。
返回 `SessionReply`：`text` 为展示内容，`pending` 表示需确认，`failed` 表示
会话已停止；确认命令仍经 `send` 提交。同一会话串行处理提交，运行发生未捕获
异常后停止，不重试结果未知的写入。调用方需在新会话重试前核实业务数据。

`create_haven(model=...)` 返回已编译的顶层图，高血压子图只编译一次并直接
挂载为节点，检查点由顶层统一提供；导入模块时不构建模型。直接调用 `ainvoke` 时必须提供稳定的
`configurable.thread_id`，并自行处理 `__interrupt__` 和 `Command(resume=...)`。
`await run_once(text, model=...)` 保留单次便利接口。模型密钥由调用方准备到
进程环境，CLI 会自行加载 `.env`。直接调用原生图的调用方需遵守同线程串行、
待确认时只恢复审批的约定，并在恢复前确定性检查紧急输入；推荐使用封装这些
约束的 `HavenSession`。

会话接口和 CLI 继续关闭 LangSmith 追踪，不改变 MDA 的追踪配置。直接使用
原生图的调用方需自行控制追踪。可选 LangSmith、持久检查点、外部 MCP、HTTP
服务和自管部署留到后续阶段，本阶段不接入。

离线验证（模拟模型与数据库边界，不访问外部服务）：

```bash
uv run python -m unittest discover -s tests -v
```
