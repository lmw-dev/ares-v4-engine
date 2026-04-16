"""
Ares v4.0 - 终端美化与执行日志模块
使用 rich 库提供结构化、可读的日志输出。
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box


console = Console()

ARES_BANNER = """
[bold cyan]
  █████╗ ██████╗ ███████╗███████╗
 ██╔══██╗██╔══██╗██╔════╝██╔════╝
 ███████║██████╔╝█████╗  ███████╗
 ██╔══██║██╔══██╗██╔══╝  ╚════██║
 ██║  ██║██║  ██║███████╗███████║
 ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚══════╝
[/bold cyan]
[bold white]v4.0 动态战术压力审计引擎[/bold white]
"""


def setup_logger(name: str = "ares", log_file: Optional[str] = None) -> logging.Logger:
    """配置并返回带 Rich 美化的 logger。"""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    handlers: list[logging.Handler] = [
        RichHandler(
            console=console,
            rich_tracebacks=True,
            markup=True,
            show_time=True,
            show_path=False,
        )
    ]

    if log_file:
        file_handler = logging.FileHandler(
            log_dir / log_file, encoding="utf-8"
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
        )
        handlers.append(file_handler)

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%H:%M:%S]",
        handlers=handlers,
        force=True,
    )

    return logging.getLogger(name)


def print_banner() -> None:
    """打印 Ares 启动横幅。"""
    console.print(Panel(ARES_BANNER, border_style="cyan", expand=False))
    console.print(
        f"[dim]启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]\n"
    )


def print_audit_header(team_name: str, match_info: str = "") -> None:
    """打印单场审计开始的标题栏。"""
    title = f"[bold yellow]⚔️  审计目标: {team_name}[/bold yellow]"
    if match_info:
        title += f"  [dim]({match_info})[/dim]"
    console.print(Panel(title, border_style="yellow"))


def print_entropy_result(
    team_name: str,
    s_base: float,
    s_dynamic: float,
    threshold: float,
    key_nodes: list[str],
    status: str,
) -> None:
    """以表格形式打印熵值计算结果。"""
    table = Table(
        title=f"[bold]战术稳定性熵值报告 - {team_name}[/bold]",
        box=box.ROUNDED,
        border_style="cyan",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("参数", style="dim", width=20)
    table.add_column("数值", justify="right")

    table.add_row("基础熵值 (S_base)", f"{s_base:.3f}")
    table.add_row("动态熵值 (S_dynamic)", f"[bold]{s_dynamic:.3f}[/bold]")
    table.add_row("预警阈值", f"{threshold:.3f}")
    table.add_row("关键节点", ", ".join(key_nodes) if key_nodes else "N/A")

    status_color = "red bold" if status == "CRITICAL_WARNING" else "green bold"
    table.add_row("状态", f"[{status_color}]{status}[/{status_color}]")

    console.print(table)


def print_simulation_result(scenario: str, result: str, ev_tag: str = "") -> None:
    """打印单个 What-If 场景的模拟结果。"""
    ev_display = f"  [bold green][EV+][/bold green]" if ev_tag == "EV+" else ""
    console.print(
        Panel(
            f"[bold cyan]{scenario}[/bold cyan]{ev_display}\n\n{result}",
            border_style="blue",
            expand=False,
        )
    )


def print_warning(message: str) -> None:
    """打印 ⚠️ 警告信息。"""
    console.print(f"[bold yellow]⚠️  {message}[/bold yellow]")


def print_critical(message: str) -> None:
    """打印 🚨 严重警告信息。"""
    console.print(
        Panel(f"[bold red]🚨 CRITICAL: {message}[/bold red]", border_style="red")
    )


def print_halt(reason: str) -> None:
    """触发停机规则时的输出。"""
    console.print(
        Panel(
            f"[bold red]🛑 执行停机\n\n原因: {reason}\n\n当前关键赛果未确认，暂停正式复盘。[/bold red]",
            border_style="red",
            title="HALT",
        )
    )


def print_success(message: str) -> None:
    """打印成功消息。"""
    console.print(f"[bold green]✅ {message}[/bold green]")


def print_info(message: str) -> None:
    """打印普通信息。"""
    console.print(f"[cyan]ℹ️  {message}[/cyan]")
