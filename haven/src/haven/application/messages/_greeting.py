"""问好、能力介绍与命令帮助。"""

from __future__ import annotations

from haven.application.commands import (
    C_CANCEL,
    C_CONFIRM,
    C_CONSENT,
    C_HELLO,
    C_HELP,
    C_PROFILE,
    C_SKIP,
    C_TREND,
    COMMANDS,
)

greeting = "你好，我是健健，你的私人健康顾问。"
ask_help = f"我可以帮你记录血压、查看趋势和建立健康档案。输入 {C_HELP} 查看全部命令。"

COMMAND_DESCRIPTIONS = {
    C_HELLO: "问好",
    C_HELP: "查看全部命令",
    C_CONSENT: "同意隐私政策",
    C_PROFILE: "创建或更新健康档案",
    C_TREND: "查看最近 7 天血压趋势",
    C_CONFIRM: "确认当前待保存内容",
    C_CANCEL: "取消当前操作，不保存",
    C_SKIP: "跳过建档可选项",
}


def help_text() -> str:
    """按命令注册顺序展示帮助。"""
    lines = ["全部命令（也可直接用中文表达）："]
    lines.extend(f"{token}：{COMMAND_DESCRIPTIONS[token]}" for token in COMMANDS)
    lines.append("记录血压可直接说数值，如 120/80。")
    return "\n".join(lines)
