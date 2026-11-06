"""集中式环境配置 —— `.env` 可调项的唯一入口（config 包）。

只放运行/部署类配置（数据库地址、模型名、日志级别）。
健康与安全相关的医学/合规常量（血压阈值、紧急词表、异常确认窗口、
固定文案等）刻意不在此 —— 它们是产品语义而非部署参数，改动须走
代码评审，不允许被环境变量在部署时悄悄改写。

**为什么配置是一个包而不是一个文件**：`haven.config.mcp` 处在构建期被
mda CLI 的裸解释器导入的连接器链路上，必须纯标准库；本模块（pydantic）
因此与它分开，且 `haven.config.__init__` 保持为空 —— 详见 `config/mcp.py`
模块头，改动包结构前先读那一段。
"""

import logging
from functools import lru_cache
from typing import TYPE_CHECKING, Literal

from langchain.chat_models import init_chat_model
from langchain.rate_limiters import InMemoryRateLimiter
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from haven.config.mcp import load_mcp_servers
from haven.config.model import AgentModelSettings, load_model_name, load_model_params
from haven.config.paths import env_file

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

_logger = logging.getLogger(__name__)

#: 开发默认 SQLite；部署须为 PostgreSQL（同一 SQLAlchemy URL 互换）。
DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///./.data/haven.db"
#: MCP 工具调用的默认超时（秒）——单次外部调用不许拖住整轮对话。
DEFAULT_MCP_TOOL_TIMEOUT = 20.0


class McpServerSettings(BaseModel):
    """一个远程 MCP 服务器的声明。

    平台只支持**远程 HTTP/SSE**（stdio 被 MDA 显式拒绝），所以这里没有
    command/args 之类的字段。`include_tools` 是**只读白名单**：留空则该
    服务器整体不启用（不给模型暴露任何它的工具）。
    """

    transport: Literal["http", "sse"] = "http"
    url: str
    #: 认证等静态请求头（如 {"Authorization": "Bearer …"}）——密钥放 .env，
    #: 在 config/mcp.json 里用 ${VAR} 引用（见 config/mcp.py）；清单可提交。
    headers: dict[str, str] = Field(default_factory=dict)
    #: 允许暴露给模型的远端工具名（白名单，只读工具）。
    include_tools: list[str] = Field(default_factory=list)
    default_tool_timeout: float = DEFAULT_MCP_TOOL_TIMEOUT


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
        # .env 中留空的占位（DATABASE_URL=）不覆盖默认值。
        env_ignore_empty=True,
    )

    database_url: str = DEFAULT_DATABASE_URL
    #: 模型名称 —— 从 `config/model.yaml` 的 `model.name` 读取；
    #: YAML 缺失/未写则取代码默认值（DEFAULT_AGENT_MODEL）。
    agent_model: str = Field(default_factory=load_model_name)
    #: 本部署唯一服务的用户 id —— **一个部署 = 一个人**（单租户）。
    #: 所有健康数据都记在这个 id 名下；它来自配置而非平台注入，所以本机
    #: `mda dev` 与云端部署是同一个人、同一份数据。不是密钥，改它=换个人用。
    #: 详见 ADR-006 与 `storage/database.py:caller_user_id`。
    owner_id: str = Field(default="owner", validation_alias="HAVEN_OWNER_ID")
    log_level: str = "INFO"

    @property
    def model_params(self) -> AgentModelSettings:
        """模型推理参数 —— YAML 覆盖默认值，YAML 缺失/缺项则取默认值。

        读取 `config/model.yaml`（与 `config/mcp.json` 同目录）；
        YAML 中未写的项自动取 `AgentModelSettings` 的 Field(default=...)。
        解析结果已缓存，调用路径上无 IO 开销。
        """
        defaults = AgentModelSettings()
        try:
            yaml_values = load_model_params()
            if yaml_values:
                return AgentModelSettings(**{**defaults.model_dump(), **yaml_values})
        except Exception as exc:  # noqa: BLE001 —— 配错了不炸启动
            _logger.warning("config/model.yaml 解析失败，使用默认值：%s", exc)
        return defaults

    @property
    def rate_limiter(self) -> InMemoryRateLimiter | None:
        """基于 `model.params` 构建的令牌桶速率限制器。

        `requests_per_minute=0` 时返回 None（不限速）；
        否则将 rpm 换算为 rps，以 `max_concurrent_requests` 作为最大突发容量。
        """
        params = self.model_params
        if params.requests_per_minute == 0:
            return None
        return InMemoryRateLimiter(
            requests_per_second=params.requests_per_minute / 60.0,
            check_every_n_seconds=params.rate_limit_check_interval,
            max_bucket_size=params.max_concurrent_requests,
        )

    @property
    def chat_model(self) -> BaseChatModel:
        """基于当前配置构建的聊天模型实例。

        `agent.py` 只需取此属性即可获得完整配置的模型；
        所有推理参数（temperature、max_tokens 等）已在底层通过
        `init_chat_model` 注入，调用方无需感知细节。
        """
        params = self.model_params
        return init_chat_model(
            self.agent_model,
            temperature=params.temperature,
            max_tokens=params.max_tokens,
            top_p=params.top_p,
            frequency_penalty=params.frequency_penalty,
            presence_penalty=params.presence_penalty,
            timeout=params.timeout,
            max_retries=params.max_retries,
            rate_limiter=self.rate_limiter,
        )

    @property
    def structured_chat_model(self) -> BaseChatModel:
        """构建一个**不带 tools 的纯推理模型**（参数与主模型一致）。

        用于需要结构化输出的独立调用（安全分类、意图识别等），
        不替代传给 `define_deep_agent` 的主模型 `chat_model`。
        调用时按需绑定 schema：
            model = settings.structured_chat_model.with_structured_output(SomeSchema)

        温度固定为 0.0（分类/判断类任务），其他参数复用主模型配置。
        """
        params = self.model_params
        return init_chat_model(
            self.agent_model,
            temperature=0.0,
            max_tokens=params.max_tokens,
            top_p=params.top_p,
            timeout=params.timeout,
            max_retries=params.max_retries,
            rate_limiter=self.rate_limiter,
        )

    @property
    def mcp_servers(self) -> dict[str, McpServerSettings]:
        """已启用的外部 MCP 服务器（校验后）。

        读取交给 `config.mcp.load_mcp_servers()`（标准库实现 —— 连接器模块
        必须在 CLI 的裸解释器里可导入，见其模块头）。配置有问题时**关闭
        MCP**（fail-closed）并留告警：策略层宁可不放行，也不放行没校验过的
        东西。解析结果已缓存，工具调用路径上无重复开销。
        """
        try:
            return {
                name: McpServerSettings.model_validate(raw)
                for name, raw in load_mcp_servers().items()
            }
        except Exception as exc:  # noqa: BLE001 —— fail-closed
            _logger.warning(
                "config/mcp.json 配置无效，MCP 已关闭（fail-closed）：%s", exc
            )
            return {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
