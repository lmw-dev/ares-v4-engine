"""
Ares v4.0 - What-If 压力测试模拟引擎

执行流程：
  1. 接收关键节点名单与战术档案
  2. 在 ChromaDB 中检索历史战术坍塌案例 (Top-K)
  3. 组装 What-If Prompt，调用 GPT-4o 执行逻辑推演
  4. 覆盖场景：核心坍塌 / 比分高压 / 决策异化
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings

from src.utils.llm_client import call_llm, load_llm_config, LLMConfig
from src.utils.logger import setup_logger

logger = setup_logger("ares.simulator")

HALT_MARKER = "[Unknown: Insufficient Resilience Data]"


def _canonical_team_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", normalized).strip("_")
    return cleaned or str(value).strip().replace(" ", "_")


@dataclass
class SimulationScenario:
    """单个 What-If 场景的定义。"""

    name: str
    query: str
    weight: float = 1.0


@dataclass
class ScenarioResult:
    """单个场景的模拟结果。"""

    scenario_name: str
    retrieved_contexts: list[str]
    llm_analysis: str
    success_rate_estimate: float
    halted: bool = False

    @property
    def is_halted(self) -> bool:
        return self.halted or self.llm_analysis == HALT_MARKER


@dataclass
class SimulationReport:
    """全场景压力测试完整报告。"""

    team_name: str
    scenario_results: list[ScenarioResult] = field(default_factory=list)
    s_dynamic_computed: float = 0.0
    overall_resilience_score: float = 0.0
    halt_triggered: bool = False

    def summary(self) -> str:
        if self.halt_triggered:
            return f"[{self.team_name}] 停机: RAG 库逆境样本不足，无法输出结论。"
        lines = [f"[{self.team_name}] 压力测试报告"]
        for r in self.scenario_results:
            status = "🛑 HALT" if r.is_halted else f"成功率预估={r.success_rate_estimate:.0%}"
            lines.append(f"  • {r.scenario_name}: {status}")
        lines.append(f"  整体韧性评分: {self.overall_resilience_score:.3f}")
        return "\n".join(lines)


# ── ChromaDB 客户端工厂 ───────────────────────────────────────────────────────

def _get_chroma_client(persist_directory: str = "./chromadb") -> chromadb.PersistentClient:
    """初始化并返回 ChromaDB 持久化客户端。"""
    persist_path = Path(persist_directory)
    if not persist_path.is_absolute():
        persist_path = Path(__file__).resolve().parents[2] / persist_path
    return chromadb.PersistentClient(
        path=str(persist_path),
        settings=Settings(anonymized_telemetry=False),
    )


def _get_or_create_collection(
    client: chromadb.PersistentClient,
    collection_name: str = "ares_tactical_memory",
) -> chromadb.Collection:
    """获取或创建 RAG 向量集合。"""
    return client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )


# ── RAG 检索 ─────────────────────────────────────────────────────────────────

def retrieve_contexts(
    collection: chromadb.Collection,
    query: str,
    top_k: int = 3,
    filter_metadata: Optional[dict] = None,
) -> list[str]:
    """
    在 ChromaDB 中执行相似度检索，返回 Top-K 历史文本片段。

    Returns:
        文本片段列表；若集合为空则返回空列表。
    """
    try:
        count = collection.count()
    except Exception:
        count = 0

    if count == 0:
        logger.warning("ChromaDB 集合为空，无历史样本可检索。")
        return []

    query_params: dict = {"query_texts": [query], "n_results": min(top_k, count)}
    if filter_metadata:
        query_params["where"] = filter_metadata

    results = collection.query(**query_params)
    documents = results.get("documents", [[]])[0]
    return [doc for doc in documents if doc]


def add_document_to_rag(
    collection: chromadb.Collection,
    doc_id: str,
    content: str,
    metadata: Optional[dict] = None,
) -> None:
    """向 RAG 集合添加单条文档（用于初始化/更新知识库）。"""
    collection.upsert(
        ids=[doc_id],
        documents=[content],
        metadatas=[metadata or {}],
    )
    logger.info(f"RAG 文档已写入: {doc_id}")


# ── What-If Prompt 构建 ──────────────────────────────────────────────────────

_SYSTEM_PROMPT = """你是 Ares 战术分析引擎的核心推演模块。
你的任务是基于给定的历史战术案例，对球队在特定压力情景下的战术表现进行客观评估。

规则：
1. 仅基于提供的历史案例进行推理，不得捏造数据。
2. 如果案例样本不足以支撑结论，明确输出: """ + HALT_MARKER + """
3. 你必须严格输出以下结构：
   情景分析: <2-3句>
   成功率预估: <0-100之间的整数百分比，必须带%号>
   关键风险点:
   - <风险点1>
   - <风险点2，可选>
"""


def _build_whatif_prompt(
    team_name: str,
    scenario: SimulationScenario,
    contexts: list[str],
    tactical_logic: dict[str, str],
) -> str:
    """构建发送给 LLM 的 What-If 推演 Prompt。"""
    context_block = "\n".join(
        [f"[历史案例{i+1}]\n{ctx}" for i, ctx in enumerate(contexts)]
    ) if contexts else "暂无历史案例（RAG 库样本不足）"

    tactic_str = " | ".join(f"{k}={v}" for k, v in tactical_logic.items())

    return f"""
## 球队: {team_name}
## 当前战术矩阵: {tactic_str}
## 压力情景: {scenario.name}
## 情景描述: {scenario.query}

## 历史 RAG 检索结果:
{context_block}

请基于以上信息，对该球队在此压力情景下的战术鲁棒性进行推演评估。
注意：必须包含一行 `成功率预估: XX%`，否则输出视为无效。
"""


# ── LLM 调用 ─────────────────────────────────────────────────────────────────

def _call_llm(prompt: str, llm_config: Optional[LLMConfig] = None) -> str:
    """调用 LLM 执行 What-If 逻辑推演（provider 由环境变量决定）。"""
    try:
        return call_llm(
            prompt=prompt,
            system_prompt=_SYSTEM_PROMPT,
            config=llm_config,
            temperature=0.2,
            max_tokens=600,
        )
    except Exception as exc:
        logger.error(f"LLM 推演失败，触发停机占位结果: {exc}")
        return HALT_MARKER


def _extract_success_rate(llm_output: str) -> float:
    """从 LLM 输出中提取概率数字（简单启发式解析）。"""
    if HALT_MARKER in llm_output:
        return 0.0

    labeled_patterns = [
        r"成功率预估[:：]\s*([0-9]+(?:\.[0-9]+)?)\s*%?",
        r"成功概率[:：]\s*([0-9]+(?:\.[0-9]+)?)\s*%?",
        r"概率预估[:：]\s*([0-9]+(?:\.[0-9]+)?)\s*%?",
        r"坍塌概率[:：]\s*([0-9]+(?:\.[0-9]+)?)\s*%?",
        r"退化概率[:：]\s*([0-9]+(?:\.[0-9]+)?)\s*%?",
    ]
    for pattern in labeled_patterns:
        match = re.search(pattern, llm_output)
        if not match:
            continue
        try:
            value = float(match.group(1))
        except ValueError:
            continue
        if value <= 1.0:
            return round(value, 3)
        if value <= 100.0:
            return round(value / 100.0, 3)

    matches = re.findall(r"(\d{1,3})\s*%", llm_output)
    if not matches:
        return 0.0
    rates = [int(m) / 100.0 for m in matches]
    return round(sum(rates) / len(rates), 3)


# ── 主模拟器 ─────────────────────────────────────────────────────────────────

def build_scenarios(key_node_dependency: list[str]) -> list[SimulationScenario]:
    """
    根据关键节点名单构建三大压力情景。

    场景A: 核心坍塌（关键球员缺阵）
    场景B: 比分高压（落后且超过60分钟）
    场景C: 决策异化（高压环境下决策质量下降）
    """
    nodes_str = "、".join(key_node_dependency) if key_node_dependency else "关键球员"

    return [
        SimulationScenario(
            name="场景A - 核心坍塌",
            query=(
                f"球队在失去 {nodes_str} 时的战术运转表现与失误。"
                "核心节点被封锁或伤停后，整体进攻组织和防守结构如何退化？"
            ),
            weight=0.5,
        ),
        SimulationScenario(
            name="场景B - 比分高压",
            query=(
                "球队在落后且比赛超过60分钟时的战术变阵与失误记录。"
                "防线 Line Height 在高压环境下的退化趋势。"
            ),
            weight=0.3,
        ),
        SimulationScenario(
            name="场景C - 决策异化",
            query=(
                "高压环境下球员决策失误率的历史记录。"
                "关键传球失误、错误跑位等决策质量指标的预期降幅。"
            ),
            weight=0.2,
        ),
    ]


def run_pressure_test(
    team_name: str,
    key_node_dependency: list[str],
    tactical_logic: dict[str, str],
    absent_players: list[str] = None,
    collection: Optional[chromadb.Collection] = None,
    persist_directory: str = "./chromadb",
    collection_name: str = "ares_tactical_memory",
    top_k: int = 3,
    llm_config: Optional[LLMConfig] = None,
) -> SimulationReport:
    """
    执行完整的 What-If 压力测试。

    Args:
        team_name:             球队名称。
        key_node_dependency:   关键节点名单。
        tactical_logic:        战术矩阵字典。
        absent_players:        已确认缺阵球员列表（可选）。
        collection:            已初始化的 ChromaDB collection（可选，不传则自动初始化）。
        persist_directory:     ChromaDB 持久化路径。
        collection_name:       向量集合名称。
        top_k:                 RAG 检索 Top-K 数量。
        llm_config:            LLM 配置（默认从环境变量自动加载）。

    Returns:
        SimulationReport 对象。
    """
    if llm_config is None:
        llm_config = load_llm_config()
    if absent_players is None:
        absent_players = []

    if collection is None:
        client = _get_chroma_client(persist_directory)
        collection = _get_or_create_collection(client, collection_name)

    scenarios = build_scenarios(key_node_dependency)
    report = SimulationReport(team_name=team_name)
    scenario_results: list[ScenarioResult] = []
    halt_triggered = False

    for scenario in scenarios:
        logger.info(f"[{team_name}] 执行 {scenario.name}...")

        filter_meta = {"team": _canonical_team_key(team_name)}
        query_text = f"{team_name} {scenario.query}".strip()
        contexts = retrieve_contexts(
            collection,
            query_text,
            top_k=top_k,
            filter_metadata=filter_meta,
        )

        # 停机检查：场景A 无样本时强制输出 Unknown
        if "核心坍塌" in scenario.name and not contexts:
            logger.warning(
                f"[{team_name}] RAG 库无逆境样本，触发停机规则。"
            )
            scenario_results.append(
                ScenarioResult(
                    scenario_name=scenario.name,
                    retrieved_contexts=[],
                    llm_analysis=HALT_MARKER,
                    success_rate_estimate=0.0,
                    halted=True,
                )
            )
            halt_triggered = True
            continue

        prompt = _build_whatif_prompt(
            team_name=team_name,
            scenario=scenario,
            contexts=contexts,
            tactical_logic=tactical_logic,
        )

        llm_output = _call_llm(prompt, llm_config=llm_config)
        success_rate = _extract_success_rate(llm_output)

        scenario_results.append(
            ScenarioResult(
                scenario_name=scenario.name,
                retrieved_contexts=contexts,
                llm_analysis=llm_output,
                success_rate_estimate=success_rate,
                halted=HALT_MARKER in llm_output,
            )
        )
        if HALT_MARKER in llm_output:
            halt_triggered = True
        logger.info(f"[{team_name}] {scenario.name} 完成，成功率预估={success_rate:.0%}")

    # 计算加权整体韧性评分
    total_weight = sum(s.weight for s in scenarios)
    weighted_sum = 0.0
    for scenario, result in zip(scenarios, scenario_results):
        if not result.is_halted:
            weighted_sum += result.success_rate_estimate * scenario.weight

    resilience = weighted_sum / total_weight if total_weight > 0 else 0.0

    report.scenario_results = scenario_results
    report.halt_triggered = halt_triggered
    report.overall_resilience_score = round(resilience, 4)

    logger.info(report.summary())
    return report
