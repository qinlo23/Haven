"""命令 token、注册与精确匹配；展示文案见 application.messages。"""

from __future__ import annotations

# ── 命令 token（单处定义，各处引用） ──
C_HELLO = "/hello"
C_HELP = "/help"
C_CONSENT = "/consent"
C_PROFILE = "/profile"
C_TREND = "/trend"
C_CONFIRM = "/confirm"
C_CANCEL = "/cancel"
C_SKIP = "/skip"

#: 注册顺序也是帮助展示顺序。
COMMANDS: tuple[str, ...] = (
    C_HELLO,
    C_HELP,
    C_CONSENT,
    C_PROFILE,
    C_TREND,
    C_CONFIRM,
    C_CANCEL,
    C_SKIP,
)

#: 中间件在模型前确定性处理的命令。
STATIC_COMMANDS: frozenset[str] = frozenset((C_HELLO, C_HELP))

#: 异步路径确定性处理（写库）的命令。
DETERMINISTIC_COMMANDS: frozenset[str] = frozenset((C_CONSENT, C_CANCEL))


def match_command(text: str) -> str | None:
    """消息整条恰为命令（去首尾空白）→ 返回命令；否则 None。"""
    token = text.strip()
    if token in COMMANDS:
        return token
    return None


_GREETING_WORDS = frozenset(("你好", "您好", "嗨", "哈喽"))


def is_greeting(text: str) -> bool:
    """仅匹配整条问好，允许首尾空白和常见句末标点。"""
    return text.strip().rstrip("。！!？?．.，,～~…").rstrip() in _GREETING_WORDS
