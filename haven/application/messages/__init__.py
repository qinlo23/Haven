"""面向用户的固定文案入口。

子模块仅定义常量和纯格式化函数；不读库、不依赖 agent 运行时。
安全及隐私正文仍以 safety 中的定义为准。
顶层 messages 包负责对话消息构造，不负责产品文案。
"""

from application.messages import (
    _account,
    _blood_pressure,
    _consent,
    _general,
    _greeting,
    _onboarding,
    _trends,
)


class _Messages:
    """显式聚合各场景文案，保持 MSG 调用接口。"""

    __slots__ = ()
    empty_input = _general.empty_input
    error_fallback = _general.error_fallback
    cancel_none = _general.cancel_none
    identity_required = _general.identity_required
    consent_required = _consent.consent_required
    consent_already = _consent.consent_already
    consent_granted = _consent.consent_granted
    consent_guidance = _consent.consent_guidance
    bp_invalid = _blood_pressure.bp_invalid
    bp_cancelled = _blood_pressure.bp_cancelled
    bp_need_value = _blood_pressure.bp_need_value
    bp_recorded = staticmethod(_blood_pressure.bp_recorded)
    bp_confirmed = staticmethod(_blood_pressure.bp_confirmed)
    bp_confirm_guidance = _blood_pressure.bp_confirm_guidance
    bp_parse_time_error = _blood_pressure.bp_parse_time_error
    bp_future_time_error = _blood_pressure.bp_future_time_error
    bp_confirm_miss = _blood_pressure.bp_confirm_miss
    bp_no_pending = _blood_pressure.bp_no_pending
    bp_duplicate = staticmethod(_blood_pressure.bp_duplicate)
    onboarding_cancelled = _onboarding.onboarding_cancelled
    onboarding_done = staticmethod(_onboarding.onboarding_done)
    greeting = _greeting.greeting
    ask_help = _greeting.ask_help
    help_text = staticmethod(_greeting.help_text)
    delete_done = _account.delete_done
    format_trend_message = staticmethod(_trends.format_trend_message)

    profile_text = staticmethod(_onboarding.profile_text)
    profile_missing = _onboarding.profile_missing
    onboarding_required_field = staticmethod(_onboarding.onboarding_required_field)
    onboarding_unknown_field = staticmethod(_onboarding.onboarding_unknown_field)
    onboarding_invalid_diagnosed_date = staticmethod(_onboarding.onboarding_invalid_diagnosed_date)
    onboarding_future_diagnosed_date = staticmethod(_onboarding.onboarding_future_diagnosed_date)
    onboarding_long_nickname = staticmethod(_onboarding.onboarding_long_nickname)
    onboarding_invalid_gender = staticmethod(_onboarding.onboarding_invalid_gender)
    onboarding_invalid_birth_date = staticmethod(_onboarding.onboarding_invalid_birth_date)
    onboarding_invalid_height = staticmethod(_onboarding.onboarding_invalid_height)
    onboarding_invalid_weight = staticmethod(_onboarding.onboarding_invalid_weight)
    onboarding_unsupported_disease = _onboarding.onboarding_unsupported_disease
    bp_invalid_systolic = staticmethod(_blood_pressure.bp_invalid_systolic)
    bp_invalid_diastolic = staticmethod(_blood_pressure.bp_invalid_diastolic)
    bp_invalid_order = staticmethod(_blood_pressure.bp_invalid_order)


MSG = _Messages()
