"""
Ares v4.0 动态战术压力审计引擎 - 主程序入口

用法:
  python main.py audit --team "曼城 (Man City)" --absent Rodri --odds 1.85 2.80 4.50
  python main.py scan
  python main.py add-doc --file path/to/analysis.txt --team "曼城 (Man City)"
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import click
import yaml
from dotenv import load_dotenv

load_dotenv(override=True)

from src.data.obsidian import scan_vault, load_team_profile, TacticalProfile
from src.engine.entropy import EntropyInput, compute_entropy
from src.engine.simulator import (
    run_pressure_test,
    _get_chroma_client,
    _get_or_create_collection,
    add_document_to_rag,
)
from src.integrations.market import OddsInput, compute_ev
from src.utils.llm_client import load_llm_config, LLMConfig
from src.integrations.delivery import deliver_results
from src.utils.logger import (
    setup_logger,
    print_banner,
    print_audit_header,
    print_entropy_result,
    print_simulation_result,
    print_halt,
    print_success,
    print_info,
    print_warning,
    console,
)

logger = setup_logger("ares.main", log_file="ares_audit.log")


def _load_config() -> dict:
    """加载 config.yaml 全局配置。"""
    config_path = Path(__file__).parent / "config.yaml"
    if not config_path.exists():
        return {}
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ── CLI 主命令组 ──────────────────────────────────────────────────────────────

@click.group()
def cli():
    """Ares v4.0 动态战术压力审计引擎 CLI"""
    pass


# ── audit 命令：对单个球队执行完整审计 ───────────────────────────────────────

@cli.command()
@click.option("--team", "-t", required=True, help="球队名称（与 Obsidian 档案文件名一致）")
@click.option(
    "--absent", "-a", multiple=True,
    help="确认缺阵的关键球员（可多次使用，例: -a Rodri -a De Bruyne）"
)
@click.option(
    "--locked", "-l", multiple=True,
    help="被战术锁死的关键球员（可多次使用）"
)
@click.option(
    "--odds", "-o", nargs=3, type=float, default=None,
    metavar="HOME DRAW AWAY",
    help="三路赔率，顺序为 主场 平局 客场（例: 1.85 3.20 4.50）"
)
@click.option("--away", is_flag=True, default=False, help="指定目标球队为客队")
@click.option(
    "--trailing", is_flag=True, default=False,
    help="模拟落后且超过60分钟的比分压力场景"
)
@click.option("--no-discord", is_flag=True, default=False, help="禁用 Discord 投递")
@click.option("--no-writeback", is_flag=True, default=False, help="禁用 Obsidian 写回")
@click.option(
    "--provider", "-p", default=None,
    type=click.Choice(["openai", "gemini", "openai_compat"], case_sensitive=False),
    help="LLM provider（覆盖 ARES_LLM_PROVIDER 环境变量）",
)
@click.option("--model", "-m", default=None, help="LLM 模型名（覆盖 ARES_LLM_MODEL 环境变量）")
@click.option("--base-url", default=None, help="API base URL（覆盖 ARES_LLM_BASE_URL，用于第三方中转）")
def audit(
    team: str,
    absent: tuple[str, ...],
    locked: tuple[str, ...],
    odds: Optional[tuple[float, float, float]],
    away: bool,
    trailing: bool,
    no_discord: bool,
    no_writeback: bool,
    provider: Optional[str],
    model: Optional[str],
    base_url: Optional[str],
):
    """对指定球队执行完整的动态战术压力审计。"""
    print_banner()
    config = _load_config()
    engine_cfg = config.get("engine", {})
    rag_cfg = config.get("rag", {})
    delivery_cfg = config.get("delivery", {})

    # CLI 参数覆盖环境变量（临时注入，不污染 .env）
    if provider:
        os.environ["ARES_LLM_PROVIDER"] = provider
    if model:
        os.environ["ARES_LLM_MODEL"] = model
    if base_url:
        os.environ["ARES_LLM_BASE_URL"] = base_url

    print_audit_header(team, match_info=" | ".join(absent) + " 缺阵" if absent else "")

    # 1. 加载 Obsidian 档案
    try:
        vault_path_str = os.environ.get("ARES_VAULT_PATH", "")
        vault_path = Path(vault_path_str).expanduser().resolve() if vault_path_str else None
        subdir = config.get("obsidian", {}).get("team_archives_subdir", "02_Team_Archives")

        if vault_path and vault_path.exists():
            profile = load_team_profile(team, vault_path=vault_path, subdir=subdir)
        else:
            print_warning(
                f"ARES_VAULT_PATH 未设置或路径不存在，尝试从 examples/ 目录加载示例档案..."
            )
            examples_dir = Path(__file__).parent / "examples"
            profile = load_team_profile(team, vault_path=examples_dir, subdir="")
    except FileNotFoundError as exc:
        console.print(f"[red]❌ 档案加载失败: {exc}[/red]")
        sys.exit(1)

    print_info(f"档案加载成功: {profile.team_name} (v{profile.version})")

    # 2. 战术衰减检查
    if profile.is_stale:
        print_warning(
            f"档案超过 {engine_cfg.get('s_source_ttl_days', 21)} 天未更新，"
            "战术标签可信度降权！"
        )

    # 3. 熵值计算
    match_context = {}
    if trailing:
        match_context = {"score_status": "Trailing", "time": "65"}

    entropy_input = EntropyInput(
        team_name=profile.team_name,
        tactical_entropy_base=profile.tactical_entropy_base,
        system_fragility_threshold=profile.system_fragility_threshold,
        key_node_dependency=profile.key_node_dependency,
        tactical_logic=profile.tactical_logic,
        absent_players=list(absent),
        locked_players=list(locked),
        match_context=match_context,
    )
    entropy_result = compute_entropy(
        entropy_input,
        key_node_absence_penalty=engine_cfg.get("key_node_absence_penalty", 0.4),
    )

    print_entropy_result(
        team_name=entropy_result.team_name,
        s_base=entropy_result.s_base,
        s_dynamic=entropy_result.s_dynamic,
        threshold=entropy_result.threshold,
        key_nodes=profile.key_node_dependency,
        status=entropy_result.status,
    )

    # 4. RAG 压力测试
    client = _get_chroma_client(rag_cfg.get("persist_directory", "./chromadb"))
    collection = _get_or_create_collection(
        client, rag_cfg.get("collection_name", "ares_tactical_memory")
    )

    llm_config = load_llm_config()
    print_info(f"LLM 配置: {llm_config.describe()}")

    simulation_report = run_pressure_test(
        team_name=profile.team_name,
        key_node_dependency=profile.key_node_dependency,
        tactical_logic=profile.tactical_logic,
        absent_players=list(absent),
        collection=collection,
        top_k=rag_cfg.get("top_k", 3),
        llm_config=llm_config,
    )

    if simulation_report.halt_triggered:
        print_halt("RAG 库中无法找到该球队在逆境下的历史样本")

    for result in simulation_report.scenario_results:
        if result.is_halted:
            console.print(
                f"[dim]⏭  {result.scenario_name}: 停机 — RAG 样本不足，Gemini 已拒绝推演[/dim]"
            )
        else:
            print_simulation_result(
                scenario=result.scenario_name,
                result=result.llm_analysis,
            )

    # 5. EV 市场解耦分析（仅在提供赔率时执行）
    ev_result = None
    if odds:
        home_odds, draw_odds, away_odds = odds
        odds_input = OddsInput(
            team_name=profile.team_name,
            home_odds=home_odds,
            draw_odds=draw_odds,
            away_odds=away_odds,
            is_home=not away,
        )
        ev_result = compute_ev(
            odds_input=odds_input,
            resilience_score=simulation_report.overall_resilience_score,
            s_dynamic=entropy_result.s_dynamic,
        )
        console.print(f"\n[bold]📊 EV 分析:[/bold] {ev_result.summary()}")

    # 6. 结果交付
    discord_enabled = delivery_cfg.get("discord_enabled", True) and not no_discord
    obsidian_writeback = delivery_cfg.get("obsidian_writeback", True) and not no_writeback

    delivery_results = deliver_results(
        entropy=entropy_result,
        simulation=simulation_report,
        ev=ev_result,
        obsidian_file=profile.file_path if obsidian_writeback else None,
        discord_enabled=discord_enabled,
        obsidian_writeback=obsidian_writeback,
    )

    if delivery_results.get("discord"):
        print_success("审计结果已投递至 Discord")
    if delivery_results.get("obsidian"):
        print_success("审计结果已写回 Obsidian 档案")

    print_success(f"审计完成: {profile.team_name}")


# ── scan 命令：批量扫描所有档案 ──────────────────────────────────────────────

@cli.command()
@click.option("--subdir", default=None, help="档案子目录（默认从 config.yaml 读取）")
def scan(subdir: Optional[str]):
    """批量扫描 Obsidian Vault 中所有球队档案并输出概览。"""
    print_banner()
    config = _load_config()

    vault_path_str = os.environ.get("ARES_VAULT_PATH", "")
    if not vault_path_str:
        examples_dir = Path(__file__).parent / "examples"
        print_warning(f"ARES_VAULT_PATH 未设置，扫描 examples/ 目录...")
        vault_path = examples_dir
        scan_subdir = ""
    else:
        vault_path = Path(vault_path_str).expanduser().resolve()
        scan_subdir = subdir or config.get("obsidian", {}).get(
            "team_archives_subdir", "02_Team_Archives"
        )

    result = scan_vault(vault_path=vault_path, subdir=scan_subdir)

    console.print(f"\n[bold cyan]扫描结果: {result.success_count} 个档案成功 / {result.error_count} 个失败[/bold cyan]")

    if result.profiles:
        from rich.table import Table
        from rich import box
        table = Table(box=box.SIMPLE, header_style="bold magenta")
        table.add_column("球队", style="bold")
        table.add_column("S_base", justify="right")
        table.add_column("阈值", justify="right")
        table.add_column("关键节点")
        table.add_column("战术 P/Space/F/H/SP")
        table.add_column("状态")

        for p in result.profiles:
            tl = p.tactical_logic
            tactic_str = "/".join([
                tl.get("P", "?"),
                tl.get("Space", "?"),
                tl.get("F", "?"),
                tl.get("H", "?"),
                tl.get("Set_Piece", "?"),
            ])
            stale_flag = "⚠️ 过期" if p.is_stale else "✅ 有效"
            table.add_row(
                p.team_name,
                f"{p.tactical_entropy_base:.3f}",
                f"{p.system_fragility_threshold:.3f}",
                ", ".join(p.key_node_dependency[:2]),
                tactic_str,
                stale_flag,
            )
        console.print(table)

    if result.errors:
        console.print("\n[red]解析失败的档案:[/red]")
        for err in result.errors:
            console.print(f"  [dim]{err['file']}[/dim]: {err['error']}")


# ── add-doc 命令：向 RAG 库添加情报文档 ──────────────────────────────────────

@cli.command("add-doc")
@click.option("--file", "-f", required=True, type=click.Path(exists=True), help="要导入的文本文件路径")
@click.option("--team", "-t", required=True, help="关联的球队名称")
@click.option("--source-level", "-s", default="B", type=click.Choice(["S", "A", "B"]), help="情报来源等级")
@click.option("--doc-id", default=None, help="文档 ID（默认自动生成）")
def add_doc(file: str, team: str, source_level: str, doc_id: Optional[str]):
    """向 ChromaDB RAG 知识库添加战术情报文档。"""
    config = _load_config()
    rag_cfg = config.get("rag", {})

    content = Path(file).read_text(encoding="utf-8")
    if not doc_id:
        import hashlib
        doc_id = hashlib.md5(f"{team}:{file}:{content[:100]}".encode()).hexdigest()[:12]

    client = _get_chroma_client(rag_cfg.get("persist_directory", "./chromadb"))
    collection = _get_or_create_collection(
        client, rag_cfg.get("collection_name", "ares_tactical_memory")
    )

    add_document_to_rag(
        collection=collection,
        doc_id=doc_id,
        content=content,
        metadata={"team": team, "source_level": source_level, "file": file},
    )
    print_success(f"文档已导入 RAG 库 [{source_level}级情报]: {file} → ID={doc_id}")


# ── 程序入口 ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cli()
