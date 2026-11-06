# src 布局迁移前置检查

检查日期：2026-11-06。

> 本文保留 MDA 0.6.1 的历史阻塞证据。后续已获授权升级至 0.10.1，
> 最新迁移结果见 [迁移验收记录](src-layout-migration.md)。下文“暂停”描述的是升级前状态。

## 结论

**迁移暂停，保持现有源码结构。** 本次要求 Python 3.14、独立 LangGraph 与
MDA 双入口同时可用；本机 MDA 0.6.1 的编译输出仍指定 Python 3.11，未通过
迁移的前置条件。没有修改生成配置、降低 Python 要求或升级 MDA。

本次只新增检查记录；未移动源码，未修改依赖、锁文件、数据库或云端部署。
`.python-version` 与后续打包配置待兼容性条件满足后一起落地。

## 已验证结果

| 检查项 | 结果 |
| --- | --- |
| 当前项目解释器 | Python 3.14.4 |
| MDA CLI | `mda 0.6.1 (1adff15 2026-08-25)` |
| 项目 Python 要求 | `requires-python = ">=3.14"` |
| 当前虚拟环境关键运行依赖导入 | 全部成功，见下方版本表 |
| 最小项目设置 `.python-version = 3.14` 后构建 | 成功，但生成 `python_version: "3.11"` |
| 同时提供源 `langgraph.json` 指定 3.14 后构建 | 仍生成 `python_version: "3.11"` |
| 生成镜像标签 | `_INTERNAL_docker_tag: "0.12.6-py3.11"` |
| `mda build --help` | 未提供 Python 版本选项 |
| 全量离线测试基线 | 未完成；首个用例报告 `ok` 后进程未继续输出，已主动中断 |

构建在 `/tmp` 中使用最小声明执行，没有复制 `.env` 或健康数据。
编译成功只能证明产物生成成功，不能证明 Python 3.14 部署运行兼容。
本次没有执行隔离环境重新安装、MDA 服务启动或云端验证，不能据此声称它们通过。

### 当前虚拟环境依赖

以下版本在 Python 3.14.4 中均可导入；这不是全新环境安装验收。

| 依赖 | 版本 |
| --- | --- |
| managed-deepagents | 0.6.1 |
| langchain | 1.4.0 |
| langchain-core | 1.6.2 |
| langgraph | 1.2.11 |
| deepagents | 0.7.13 |
| langchain-deepseek | 1.1.1 |
| sqlalchemy | 2.0.52 |
| aiosqlite | 0.22.1 |
| asyncpg | 0.31.0 |
| pydantic-settings | 2.15.0 |
| langchain-mcp-adapters | 0.3.2 |
| mcp | 1.30.0 |

## 最小复现

在仓库根目录运行以下脚本。仅在临时目录写入测试项目，不读取密钥，
不启动服务，不修改现有构建目录。

```python
import json
import subprocess
import tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="haven-mda-py314-"))
(root / "pyproject.toml").write_text(
    Path("pyproject.toml").read_text(encoding="utf-8"), encoding="utf-8"
)
(root / ".python-version").write_text("3.14\n", encoding="utf-8")
(root / "instructions.md").write_text("兼容性验证。\n", encoding="utf-8")
(root / "agent.py").write_text(
    "from managed_deepagents import define_deep_agent\n"
    'agent = define_deep_agent(name="haven-compat", '
    'model="deepseek:deepseek-chat")\n',
    encoding="utf-8",
)

for explicit_config in (False, True):
    if explicit_config:
        (root / "langgraph.json").write_text(
            json.dumps({
                "python_version": "3.14",
                "dependencies": ["."],
                "graphs": {"haven-compat": "./agent.py:agent"},
            }),
            encoding="utf-8",
        )
    subprocess.run(["mda", "build", str(root)], check=True)
    generated = json.loads(
        (root / ".mda/build/langgraph.json").read_text(encoding="utf-8")
    )
    print(explicit_config, generated["python_version"],
          generated.get("_INTERNAL_docker_tag"))
```

两次输出均为 Python `3.11` 与镜像标签 `0.12.6-py3.11`。

## 恢复迁移的条件

先确认受支持的 MDA Python 3.14 编译与运行路径，再恢复既定的 src 迁移计划。
如解决方案需要升级 MDA 或改变“双入口 + Python 3.14”约束，应先调整计划，
不能将手工修改生成文件当作兼容性解决方案。

恢复后仍需重新完成隔离安装、全量离线测试、wheel/源码分发包验证、资源验证、
MDA 本地启动及静态检查，不能沿用此次未完成的验收结果。
