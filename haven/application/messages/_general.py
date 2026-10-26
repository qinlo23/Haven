"""通用兜底消息。"""

from __future__ import annotations

from safety.degradation import LLM_DEGRADED_RESPONSE

empty_input = "请告诉我你的需求，我会尽力帮助你。"
error_fallback = LLM_DEGRADED_RESPONSE

cancel_none = "已取消当前操作。"
identity_required = "本服务只为指定用户提供服务，当前调用者不在服务范围内。"
