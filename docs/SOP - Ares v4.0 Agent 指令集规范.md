---
tags:
  - area/football-matrix
  - type/sop
  - ares/v4-agent
status: active
version: 1
creation_date: 2026-04-15
last_modified_date: 2026-04-15
related:
  - "[[体系 - Ares Football 深度分析与 RAG 对冲系统 v3.5]]"
---

# SOP - Ares v4.0 Agent 指令集规范

> [!tip] 文档定位
本 SOP 定义了用户如何通过 Obsidian 结构化笔记驱动后台 Agent 执行“动态压力测试”。它将复杂的 Python 代码逻辑抽象为可读、可控的指令集。

## 1. 指令输入：档案参数化 (Feeding)
Agent 定期扫描 `7_Team_Archives` 目录。档案中的 YAML 区块必须包含以下“动态参数”：
- **tactical_entropy_base**: 基础熵值（由 Agent 根据变阵频率自动计算）。
- **system_fragility_threshold**: 体系脆弱性阈值（默认 0.7）。
- **key_node_dependency**: 核心节点依赖名单（如关键后腰、中卫）。
- **xG**（可选）: 赛后预期进球（float）。
- **passes_attacking_third**（可选）: 进攻三区成功传球数（int）。

## 2. 后台执行逻辑：What-If 模拟指令 (Compute)
当触发审计指令时，Python 脚本必须按以下顺序调用 RAG 库：
1.  **场景模拟 A (核心缺失)**：`SEARCH context WHERE player IN {key_node_dependency} AND status = 'Absent'`。
2.  **场景模拟 B (比分压力)**：`RETRIEVE tactics_change WHERE score_status = 'Trailing' AND time > 60`。
3.  **计算公式**：
    - `S_dynamic = S_base + Fear_Factor_Modifier + Injury_Modifier + Efficiency_Modifier`
    - `efficiency_ratio = xG / (passes_attacking_third + 1.0)`
    - 若 `efficiency_ratio > 0.08`，则 `Efficiency_Modifier = -0.15`
    - 若 `efficiency_ratio < 0.03` 且 `passes_attacking_third > 50`，则 `Efficiency_Modifier = +0.10`
    - 其他情况 `Efficiency_Modifier = 0.0`
    - 最终必须执行 `clip(S_dynamic, 0.1, 0.9)`

## 3. 事实门禁：数据纯净度校验 (Audit)
在输出结论前，Agent 必须强制执行：
- **S 级源优先原则**：若 The Athletic 或 Coaches' Voice 存在对该战术的最新修正，强制覆盖 A 级源结论。
- **停机检查**：若 RAG 库中无法找到该球队在“逆境下”的任何历史样本，强制输出 `[Unknown: Insufficient Resilience Data]`。

## 4. 结果反馈 (Feedback Loop)
- **Obsidian 更新**：Agent 自动在对应球队档案的 `## ⚔️ 压力测试记录` 节点下插入最新的模拟结果。
- **预警标记**：若 $S_{dynamic} > threshold$，在标题前缀强制添加 ⚠️ 标识。
