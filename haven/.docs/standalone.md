# Haven 独立入口：第一步

现有 `mda dev` / `mda deploy` 继续使用原有入口。新增的 `haven.py` 直接装配
LangChain Agent，复用 11 个业务工具和原有中间件，无需 MDA 编译。

```bash
uv run python haven.py "您好"
# 或只启动一次输入提示
uv run python haven.py
```

CLI 从项目根目录的 `.env` 加载模型密钥和业务配置，进程环境优先，模型参数
沿用 `config/model.yaml`。实际写入沿用 `DATABASE_URL` 和 `HAVEN_OWNER_ID`；
试运行前请通过环境变量指定独立的开发数据库，避免操作原有健康数据：

```bash
DATABASE_URL=sqlite+aiosqlite:///./.data/haven-standalone.db uv run python haven.py
```

第一步仅执行单次对话；没有多轮线程、检查点、人工批准、外部 MCP 或 HTTP
服务。异常血压确认和数据删除工具仍向模型声明，但工具钩子会固定拒绝执行。
CLI 本轮显式关闭 LangSmith 追踪，不修改 MDA 的追踪配置。

程序可调用 `create_haven(model=...)` 获取原生图并使用 `ainvoke`；模型密钥
需由调用方准备到进程环境。也可调用 `await run_once(text, model=...)`，它提供
与 CLI 相同的关闭追踪和异常降级行为。工厂导入时不会创建模型或启动服务。

后续分别实施多轮对话与人工确认、可选 LangSmith 追踪，本阶段不提前接入。

离线验证（模拟模型与数据库边界，不访问外部服务）：

```bash
uv run python -m unittest discover -s tests -v
```
