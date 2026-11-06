"""健健（Haven）—— 0.0.1 个人高血压管理 Managed Deep Agent。

- 身份：**单租户（一个部署 = 一个人，见 ADR-006）**。`identity.py` 只回答
  "能不能进"（LangSmith API key 认证）；"进来的是谁"由配置 `HAVEN_OWNER_ID`
  决定 —— 运行时注入的身份不参与授予，只用于否决真正的 `person` 身份
  （见 `storage/database.py:caller_user_id`）。
- **无 `memory.py`**：MDA 记忆（`define_memory`）是**部署级共享**的，一个
  部署里所有调用者读写同一棵树，健康数据绝不写入。
- 记忆注入 = `middleware/memory_context.py`：每次模型调用前，从库内业务表
  **确定性组装**该用户的数据摘要并入 system prompt（B2 / S1.6），只读、
  不落库、不进线程历史 —— 它和 MDA 记忆、和根目录 `memory.py` 都不是
  一回事，勿混。跨会话连续性 = 持久线程 + 库内业务表（user_id 隔离）。
- 中间件顺序：紧急输入扫描在最外层（先于一切 LLM 处理），命令路由、
  输出安全依次在后，记忆注入在安全中间件内侧（它只往 system prompt
  追加内容块，不重写，见 `middleware/memory_context.py` 模块头），
  可观测性中间件（`middleware/observability.py`）记录每次工具调用的
  耗时与结果，同意闸门（`middleware/consent_policy.py`）在工具调用前
  统一拦截，最后是 MCP 策略（`middleware/mcp_policy.py`，只挂工具调用
  钩子：外部 MCP 工具白名单放行、默认拒绝、审计、失败降级）。
- 外部 MCP：声明在根目录 `tools/mcp.py`（平台原生 define_mcp，仅远程 HTTP/SSE），
  服务器清单来自 `src/haven/config/mcp.json`；空/缺失 = 完全关闭，模型看不到任何外部工具。
- 中断门：异常血压确认与数据删除需人工批准后才执行工具
  （`interrupt_on`），配合工具的确定性闸门（待确认行匹配）。
"""

from managed_deepagents import define_deep_agent

from haven.config.settings import get_settings
from haven.middleware.commands import command_middleware
from haven.middleware.consent_policy import consent_policy_middleware
from haven.middleware.emergency_input import emergency_input_middleware
from haven.middleware.mcp_policy import mcp_policy_middleware
from haven.middleware.memory_context import memory_context_middleware
from haven.middleware.observability import observability_middleware
from haven.middleware.output_safety import output_safety_middleware
from haven.tools.account import delete_my_data
from haven.tools.consent import consent_status, get_consent_policy, record_consent
from haven.tools.onboarding import (
    get_health_profile,
    save_health_profile,
    save_onboarding_draft,
)
from haven.tools.permissions import interrupt_on
from haven.tools.trends import get_seven_day_trend
from haven.tools.vitals import (
    confirm_abnormal_blood_pressure,
    get_pending_blood_pressure,
    record_blood_pressure,
)

_middleware = [
    emergency_input_middleware,  # 最外层：急救词优先于一切命令
    command_middleware,  # /hello /help /consent /cancel 确定性路由
    output_safety_middleware,
    memory_context_middleware,  # 只追加 system prompt 内容块，放其内侧
    observability_middleware,  # 工具调用计时 + 审计，包裹下方所有工具层
    consent_policy_middleware,  # 同意闸门：从 permissions.py 查表，未同意则拒绝
    mcp_policy_middleware,  # 工具层钩子：MCP 白名单/审计/降级（只挂工具调用）
]

agent = define_deep_agent(
    name="haven",
    model=get_settings().chat_model,
    tools=[
        get_consent_policy,
        consent_status,
        record_consent,
        get_health_profile,
        save_health_profile,
        save_onboarding_draft,
        record_blood_pressure,
        get_pending_blood_pressure,
        confirm_abnormal_blood_pressure,
        get_seven_day_trend,
        delete_my_data,
    ],
    middleware=_middleware,
    # MDA 0.10 自动发布 general-purpose 独立图；它默认不继承中间件。
    # 显式复用相同顺序，确保直接调用该图也不能绕过急救、安全和同意闸门。
    subagents=[
        {
            "name": "general-purpose",
            "description": "遵循健健相同安全规则和同意闸门的通用助手。",
            "middleware": _middleware,
        }
    ],
    interrupt_on=interrupt_on(),
)
