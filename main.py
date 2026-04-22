"""
Ares v4.0 动态战术压力审计引擎 - 主程序入口

用法:
  python main.py audit --team "曼城 (Man City)" --absent Rodri --odds 1.85 2.80 4.50
  python main.py scan
  python main.py add-doc --file path/to/analysis.txt --team "曼城 (Man City)"
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

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
from src.integrations.delivery import build_audit_report, save_audit_report
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


@dataclass
class AuditExecutionResult:
    """单次球队审计的完整产物。"""

    profile: TacticalProfile
    entropy_result: object
    simulation_report: object
    ev_result: object | None
    report_md: str
    saved_path: Optional[Path] = None


def _load_config() -> dict:
    """加载 config.yaml 全局配置。"""
    config_path = Path(__file__).parent / "config.yaml"
    if not config_path.exists():
        return {}
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _safe_float(value: object, default: float = 0.0) -> float:
    """安全地将输入转换为 float，失败时返回默认值。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: object, default: int = 0) -> int:
    """安全地将输入转换为 int，失败时返回默认值。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _resolve_vault_path() -> Optional[Path]:
    """解析环境变量中的 Vault 路径。"""
    vault_path_str = os.environ.get("ARES_VAULT_PATH", "")
    if not vault_path_str:
        return None
    vault_path = Path(vault_path_str).expanduser().resolve()
    return vault_path if vault_path.exists() else None


def _load_profile_for_team(team: str, config: dict, emit_console: bool = True) -> TacticalProfile:
    """按当前配置加载单个球队档案。"""
    vault_path = _resolve_vault_path()
    subdir = config.get("obsidian", {}).get("team_archives_subdir", "02_Team_Archives")

    if vault_path is not None:
        profile = load_team_profile(team, vault_path=vault_path, subdir=subdir)
    else:
        if emit_console:
            print_warning("ARES_VAULT_PATH 未设置或路径不存在，尝试从 examples/ 目录加载示例档案...")
        examples_dir = Path(__file__).parent / "examples"
        profile = load_team_profile(team, vault_path=examples_dir, subdir="")

    if emit_console:
        print_info(f"档案加载成功: {profile.team_name} (v{profile.version})")

    return profile


def _build_match_context(
    trailing: bool,
    stakes: Optional[str],
    team_status: Optional[str],
) -> dict[str, str]:
    """构建熵值引擎所需的比赛语境。"""
    match_context: dict[str, str] = {}
    if trailing:
        match_context["score_status"] = "Trailing"
        match_context["time"] = "65"
    if stakes:
        match_context["stakes"] = stakes
    if team_status:
        match_context["team_status"] = team_status
    return match_context


def _execute_team_audit(
    *,
    team: str,
    absent: list[str],
    locked: list[str],
    odds: Optional[tuple[float, float, float]],
    away: bool,
    trailing: bool,
    stakes: Optional[str],
    team_status: Optional[str],
    points_gap: float,
    xg_gap: float,
    output_dir: Optional[Path] = None,
    output_path: Optional[Path] = None,
    save_report: bool = True,
    emit_console: bool = True,
) -> AuditExecutionResult:
    """执行单支球队的完整审计流程。"""
    config = _load_config()
    engine_cfg = config.get("engine", {})
    rag_cfg = config.get("rag", {})

    if emit_console:
        print_audit_header(team, match_info=" | ".join(absent) + " 缺阵" if absent else "")

    profile = _load_profile_for_team(team, config=config, emit_console=emit_console)

    if profile.is_stale and emit_console:
        print_warning(
            f"档案超过 {engine_cfg.get('s_source_ttl_days', 21)} 天未更新，战术标签可信度降权！"
        )

    entropy_input = EntropyInput(
        team_name=profile.team_name,
        tactical_entropy_base=profile.tactical_entropy_base,
        system_fragility_threshold=profile.system_fragility_threshold,
        key_node_dependency=profile.key_node_dependency,
        tactical_logic=profile.tactical_logic,
        absent_players=absent,
        locked_players=locked,
        match_context=_build_match_context(trailing, stakes, team_status),
        xg=_safe_float(profile.raw_metadata.get("xG", profile.raw_metadata.get("xg", 0.0)), default=0.0),
        passes_attacking_third=_safe_int(profile.raw_metadata.get("passes_attacking_third", 0), default=0),
    )
    entropy_result = compute_entropy(
        entropy_input,
        key_node_absence_penalty=engine_cfg.get("key_node_absence_penalty", 0.4),
    )

    if emit_console:
        print_entropy_result(
            team_name=entropy_result.team_name,
            s_base=entropy_result.s_base,
            s_dynamic=entropy_result.s_dynamic,
            threshold=entropy_result.threshold,
            key_nodes=profile.key_node_dependency,
            status=entropy_result.status,
        )

    client = _get_chroma_client(rag_cfg.get("persist_directory", "./chromadb"))
    collection = _get_or_create_collection(
        client, rag_cfg.get("collection_name", "ares_tactical_memory")
    )

    llm_config = load_llm_config()
    if emit_console:
        print_info(f"LLM 配置: {llm_config.describe()}")

    simulation_report = run_pressure_test(
        team_name=profile.team_name,
        key_node_dependency=profile.key_node_dependency,
        tactical_logic=profile.tactical_logic,
        absent_players=absent,
        collection=collection,
        top_k=rag_cfg.get("top_k", 3),
        llm_config=llm_config,
    )

    if simulation_report.halt_triggered and emit_console:
        print_halt("RAG 库中无法找到该球队在逆境下的历史样本")

    if emit_console:
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

    ev_result = None
    if odds:
        home_odds, draw_odds, away_odds = odds
        strength_gap_index = (points_gap * 1.0) + (xg_gap * 0.5)
        odds_input = OddsInput(
            team_name=profile.team_name,
            home_odds=home_odds,
            draw_odds=draw_odds,
            away_odds=away_odds,
            is_home=not away,
            strength_gap_index=strength_gap_index,
        )
        ev_result = compute_ev(
            odds_input=odds_input,
            resilience_score=simulation_report.overall_resilience_score,
            s_dynamic=entropy_result.s_dynamic,
        )
        if emit_console:
            console.print(f"\n[bold]📊 EV 分析:[/bold] {ev_result.summary()}")

    report_md = build_audit_report(entropy_result, simulation_report, ev_result)
    saved_path: Optional[Path] = None

    if save_report:
        vault_path = _resolve_vault_path()
        if vault_path is not None:
            try:
                saved_path = save_audit_report(
                    str(vault_path),
                    profile.team_name,
                    report_md,
                    output_dir=output_dir,
                    output_path=output_path,
                )
                if emit_console:
                    print_success(f"独立战报已落盘: {saved_path}")
            except OSError as exc:
                console.print(f"[red]❌ 战报写入失败: {exc}[/red]")
        else:
            if emit_console:
                print_warning("ARES_VAULT_PATH 未配置，战报未写入磁盘。")
                console.print(report_md)

    if emit_console:
        print_success(f"审计完成: {profile.team_name}")

    return AuditExecutionResult(
        profile=profile,
        entropy_result=entropy_result,
        simulation_report=simulation_report,
        ev_result=ev_result,
        report_md=report_md,
        saved_path=saved_path,
    )


def _load_manifest(path: Path) -> dict[str, Any]:
    """读取 issue 派发单。"""
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_manifest_path(issue: str, manifest: Optional[str]) -> Path:
    """解析 issue 对应的 dispatch manifest 路径。"""
    if manifest:
        return Path(manifest).expanduser().resolve()

    vault_path = _resolve_vault_path()
    if vault_path is not None:
        candidate = vault_path / "04_RAG_Raw_Data" / "Cold_Data_Lake" / f"{issue}_dispatch_manifest.json"
        if candidate.exists():
            return candidate

    fallback = Path.cwd() / "raw_reports" / f"{issue}_dispatch_manifest.json"
    return fallback.resolve()


def _split_match_english(english: str) -> tuple[str, str]:
    """从 manifest 的 english 字段拆分主客队。"""
    if " vs " in english:
        home, away = english.split(" vs ", 1)
        return home.strip(), away.strip()
    if " VS " in english:
        home, away = english.split(" VS ", 1)
        return home.strip(), away.strip()
    return english.strip(), "Away"


def _sanitize_segment(value: str, fallback: str = "segment") -> str:
    """规范化路径片段。"""
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value.strip())
    cleaned = cleaned.strip("_")
    return cleaned or fallback


def _latest_market_odds(match: dict[str, Any]) -> Optional[tuple[float, float, float]]:
    """提取 manifest 中最新一组欧赔。"""
    snapshots = match.get("market_odds_history") or []
    if not snapshots:
        return None
    europe = snapshots[-1].get("europe") or {}
    try:
        return (
            float(europe["win"]),
            float(europe["draw"]),
            float(europe["loss"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _rag_collection_doc_count() -> int:
    """返回当前 RAG 集合的总文档数。"""
    config = _load_config()
    rag_cfg = config.get("rag", {})
    client = _get_chroma_client(rag_cfg.get("persist_directory", "./chromadb"))
    collection = _get_or_create_collection(
        client,
        rag_cfg.get("collection_name", "ares_tactical_memory"),
    )
    try:
        return int(collection.count())
    except Exception:
        return 0


def _build_match_audit_markdown(
    *,
    issue: str,
    match: dict[str, Any],
    home_result: AuditExecutionResult,
    away_result: AuditExecutionResult,
    odds: Optional[tuple[float, float, float]],
) -> str:
    """将单场主客队推演合成为 Prematch 审计文档。"""
    header_lines = [
        f"# Ares Prematch Audit - Issue {issue} - {match.get('english', 'Unknown Match')}",
        "",
        f"- 中文对阵: `{match.get('chinese', '')}`",
        f"- 映射来源: `{match.get('mapping_source', 'unknown')}`",
        f"- Understat ID: `{match.get('understat_id', '')}`",
    ]
    if odds:
        header_lines.append(
            f"- 最新欧赔: 主 `{odds[0]:.2f}` / 平 `{odds[1]:.2f}` / 客 `{odds[2]:.2f}`"
        )

    def _team_lines(side: str, result: AuditExecutionResult) -> list[str]:
        lines = [
            f"## {side} - {result.profile.team_name}",
            f"- S_dynamic: `{result.entropy_result.s_dynamic:.3f}`",
            f"- 状态: `{result.entropy_result.status}`",
            f"- 整体韧性: `{result.simulation_report.overall_resilience_score:.3f}`",
        ]
        if result.entropy_result.risk_flags:
            lines.append("- 风险标记: " + " | ".join(result.entropy_result.risk_flags[:5]))
        if result.simulation_report.halt_triggered:
            lines.append("- Prematch 结论: `[HALT] RAG 库逆境样本不足`")
        if result.ev_result is not None:
            lines.append(
                f"- EV: `{result.ev_result.ev_tag}` | 市场 `{result.ev_result.market_implied_prob:.1%}` / 模型 `{result.ev_result.model_win_prob:.1%}`"
            )
            lines.append(f"- 决策: {result.ev_result.decision}")
        return lines

    lines = header_lines + [""] + _team_lines("Home", home_result) + [""] + _team_lines("Away", away_result)
    return "\n".join(lines) + "\n"


def _resolve_issue_output_file(vault_path: Path, issue: str, match: dict[str, Any]) -> Path:
    """为单场 Prematch 审计解析输出文件路径。"""
    prematch_dir = vault_path / "03_Match_Audits" / str(issue) / "01_Prematch_Audits"
    prematch_dir.mkdir(parents=True, exist_ok=True)
    index = _safe_int(match.get("index"), default=0)
    prefix = f"Audit-{issue}-{index:02d}-"
    existing = sorted(prematch_dir.glob(f"{prefix}*.md"))
    if existing:
        return existing[0]

    english = str(match.get("english", f"Match-{index:02d}"))
    home_team, away_team = _split_match_english(english)
    filename = f"{prefix}{_sanitize_segment(home_team, 'Home')}-vs-{_sanitize_segment(away_team, 'Away')}.md"
    return prematch_dir / filename


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
@click.option("--stakes", default=None, type=click.Choice(["relegation_battle"]), help="比赛特殊语境（如保级生死战）")
@click.option("--team-status", default=None, type=click.Choice(["relegated_no_pressure"]), help="球队特殊状态（如无压力降级）")
@click.option("--points-gap", type=float, default=0.0, help="两队场均积分差 (用于计算实力护城河)")
@click.option("--xg-gap", type=float, default=0.0, help="两队 xG 预计进球差 (用于计算实力护城河)")
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
    stakes: Optional[str],
    team_status: Optional[str],
    points_gap: float,
    xg_gap: float,
    provider: Optional[str],
    model: Optional[str],
    base_url: Optional[str],
):
    """对指定球队执行完整的动态战术压力审计。"""
    print_banner()

    # CLI 参数覆盖环境变量（临时注入，不污染 .env）
    if provider:
        os.environ["ARES_LLM_PROVIDER"] = provider
    if model:
        os.environ["ARES_LLM_MODEL"] = model
    if base_url:
        os.environ["ARES_LLM_BASE_URL"] = base_url

    try:
        _execute_team_audit(
            team=team,
            absent=list(absent),
            locked=list(locked),
            odds=odds,
            away=away,
            trailing=trailing,
            stakes=stakes,
            team_status=team_status,
            points_gap=points_gap,
            xg_gap=xg_gap,
            save_report=True,
            emit_console=True,
        )
    except FileNotFoundError as exc:
        console.print(f"[red]❌ 档案加载失败: {exc}[/red]")
        sys.exit(1)

# ── scan 命令：批量扫描所有档案 ──────────────────────────────────────────────

@cli.command("audit-issue")
@click.option("--issue", required=True, help="中国体彩期号，如 26064")
@click.option("--manifest", default=None, type=click.Path(exists=True), help="显式指定 dispatch_manifest.json 路径")
@click.option("--limit", type=int, default=None, help="仅处理前 N 场比赛（调试用）")
def audit_issue(issue: str, manifest: Optional[str], limit: Optional[int]):
    """按 issue 批量执行 Prematch 推演，并写入治理目录。"""
    print_banner()

    vault_path = _resolve_vault_path()
    if vault_path is None:
        console.print("[red]❌ audit-issue 需要有效的 ARES_VAULT_PATH。[/red]")
        sys.exit(1)

    manifest_path = _resolve_manifest_path(issue, manifest)
    if not manifest_path.exists():
        console.print(f"[red]❌ 找不到 dispatch_manifest: {manifest_path}[/red]")
        sys.exit(1)

    rag_count = _rag_collection_doc_count()
    if rag_count == 0:
        console.print("[red]❌ RAG 集合为空，无法执行 Prematch 推演。请先通过 `main.py add-doc` 导入战术文档。[/red]")
        sys.exit(2)
    print_info(f"RAG 集合已就绪: {rag_count} 条文档")

    payload = _load_manifest(manifest_path)
    matches = payload.get("matches", [])
    if not isinstance(matches, list) or not matches:
        console.print(f"[red]❌ manifest 中没有可处理的 matches: {manifest_path}[/red]")
        sys.exit(1)

    processed = 0
    failed = 0
    target_matches = matches[:limit] if limit else matches

    for match in target_matches:
        english = str(match.get("english", "")).strip()
        if not english:
            logger.warning("跳过缺少 english 字段的比赛: %s", match)
            failed += 1
            continue

        home_team, away_team = _split_match_english(english)
        odds = _latest_market_odds(match)
        output_file = _resolve_issue_output_file(vault_path, issue, match)

        print_info(f"[Issue {issue}] Prematch 推演: {english}")

        try:
            home_result = _execute_team_audit(
                team=home_team,
                absent=[],
                locked=[],
                odds=odds,
                away=False,
                trailing=False,
                stakes=None,
                team_status=None,
                points_gap=0.0,
                xg_gap=0.0,
                save_report=False,
                emit_console=False,
            )
            away_result = _execute_team_audit(
                team=away_team,
                absent=[],
                locked=[],
                odds=odds,
                away=True,
                trailing=False,
                stakes=None,
                team_status=None,
                points_gap=0.0,
                xg_gap=0.0,
                save_report=False,
                emit_console=False,
            )
        except FileNotFoundError as exc:
            logger.error("Prematch 推演失败 [%s]: %s", english, exc)
            failed += 1
            continue

        combined_report = _build_match_audit_markdown(
            issue=issue,
            match=match,
            home_result=home_result,
            away_result=away_result,
            odds=odds,
        )
        try:
            output_file.write_text(combined_report, encoding="utf-8")
        except OSError as exc:
            logger.error("Prematch 审计写入失败 [%s]: %s", english, exc)
            failed += 1
            continue
        print_success(f"Prematch 审计已写入: {output_file}")
        processed += 1

    summary_payload = {"issue": issue, "processed": processed, "failed": failed}
    print_info(f"audit-issue 完成: 成功 {processed} / 失败 {failed}")
    print(f"AUDIT_ISSUE_SUMMARY {json.dumps(summary_payload, ensure_ascii=False)}")


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
