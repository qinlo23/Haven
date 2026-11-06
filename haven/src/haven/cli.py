"""独立命令行入口：加载运行配置并处理多轮或单次会话。"""

from __future__ import annotations

import argparse
import asyncio

from dotenv import load_dotenv

from haven.config.paths import env_file
from haven.session import HavenSession, run_once


def chat() -> None:
    """本地多轮交互；退出即释放对话和确认中断的内存状态。"""
    # 主线程读取输入，避免 Ctrl+C 后执行器仍等待 input 导致退出挂起。
    # Runner 保持同一事件循环，模型的异步客户端和数据库连接可跨轮复用。
    with asyncio.Runner() as runner:
        session = HavenSession()
        print("健健独立模式：/exit 退出；退出后对话与确认中断清空，业务数据保留在数据库。")
        while True:
            text = input("您：")
            if text.strip() == "/exit":
                return
            reply = runner.run(session.send(text))
            print(f"健健：{reply.text}")
            if reply.failed:
                return


def main() -> int:
    """无参数进入多轮交互，带消息参数保留单次模式。"""
    parser = argparse.ArgumentParser(description="健健独立入口（多轮对话与人工确认）")
    parser.add_argument("message", nargs="?", help="单次消息；省略时进入多轮交互")
    args = parser.parse_args()
    try:
        # python-dotenv 已由 pydantic-settings 引入，不新增依赖或改动版本组合。
        load_dotenv(env_file(), override=False)
        if args.message is None:
            chat()
        else:
            print(asyncio.run(run_once(args.message)))
        return 0
    except EOFError:
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:  # noqa: BLE001 —— 配置或启动失败时不打印密钥和健康数据
        print("启动失败，请检查依赖、模型配置和 .env。")
        return 1

