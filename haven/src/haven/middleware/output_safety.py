"""输出安全过滤 + LLM 降级。

- 输出过滤：对模型生成结果做确定性正则检查（诊断 / 处方模式），
  命中即把该条消息**替换**为 SAFE_FALLBACK（在 wrap 层重写而非 after_model
  追加，避免不安全原文残留在线程历史里）。
- 降级：模型调用抛异常（提供商不可用 / 超时）时，返回固定降级文案
  LLM_DEGRADED_RESPONSE（S9.9 矩阵）；紧急输入扫描在更外层，
  不受影响。

工具调用类消息（tool_calls）原样放行 —— 参数由工具层确定性校验。
"""

from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware, ModelResponse
from langchain_core.messages import BaseMessage

from haven.messages.builder import builder
from haven.safety.degradation import LLM_DEGRADED_RESPONSE
from haven.safety.output_filter import SAFE_FALLBACK, filter_output


def _rewrite_safe(response: ModelResponse) -> ModelResponse:
    messages: list[BaseMessage] = []
    for message in response.result:
        content = getattr(message, "content", None)
        if isinstance(content, str) and content and filter_output(content) != content:
            messages.append(builder.ai(SAFE_FALLBACK))
        else:
            messages.append(message)
    return ModelResponse(result=messages, structured_response=response.structured_response)


class OutputSafetyMiddleware(AgentMiddleware):
    """模型输出的确定性安全重写 + 模型故障降级兜底。"""

    @property
    def name(self) -> str:
        return "haven_output_safety"

    def wrap_model_call(self, request, handler):
        try:
            return _rewrite_safe(handler(request))
        except Exception:  # noqa: BLE001 —— 模型不可用 → 固定降级文案
            return ModelResponse(result=[builder.ai(LLM_DEGRADED_RESPONSE)])

    async def awrap_model_call(self, request, handler):
        try:
            return _rewrite_safe(await handler(request))
        except Exception:  # noqa: BLE001
            return ModelResponse(result=[builder.ai(LLM_DEGRADED_RESPONSE)])


output_safety_middleware = OutputSafetyMiddleware()
