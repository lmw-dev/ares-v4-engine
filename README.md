# Ares v4.0 - 动态战术压力审计引擎

> 从"静态证据搜集"升维为"动态系统模拟"。利用 RAG 库执行压力测试，识别球队在逆境下的战术坍塌风险，捕捉市场共识与真实鲁棒性之间的非对称机会。

## 项目结构

```
ares-v4-engine/
├── .env.example             # 环境变量模板（复制为 .env 并填写）
├── .gitignore
├── config.yaml              # 全局非敏感配置
├── requirements.txt         # Python 依赖
├── main.py                  # CLI 主入口
├── docs/                    # 设计文档 (PRD / SOP / 体系)
├── examples/                # 示例 Obsidian 档案与 RAG 种子文档
└── src/
    ├── data/
    │   └── obsidian.py      # Obsidian Markdown/YAML 解析器
    ├── engine/
    │   ├── entropy.py       # 动态熵值 (S_dynamic) 计算内核
    │   └── simulator.py     # ChromaDB RAG What-If 压力测试引擎
    ├── integrations/
    │   ├── market.py        # 赔率对冲与 EV 剪刀差计算
    │   └── delivery.py      # Discord Webhook + Obsidian 写回
    └── utils/
        └── logger.py        # Rich 终端美化与执行日志
```

## 快速开始

### 1. 环境准备

```bash
# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填写：
#   ARES_VAULT_PATH   - Obsidian Vault 绝对路径
#   OPENAI_API_KEY    - GPT-4o API Key
#   DISCORD_WEBHOOK_URL - Discord Webhook（可选）
```

### 3. 导入 / 重建 RAG 情报文档

```bash
# 优先推荐：从 Vault 赛后运行态批量重建 RAG
python main.py sync-rag-runtime

# 或者：手工导入单条战术分析文章
python main.py add-doc \
  --file examples/rag_seed_manchestercity.txt \
  --team "曼城 (Man City)" \
  --source-level S
```

说明：
- `sync-rag-runtime` 会读取 `$ARES_VAULT_PATH/02_Team_Archives/_Postmatch_Runtime/**/*.jsonl`
- 自动把赛后物理事实转成可检索的 Prematch RAG 样本
- `chromadb` 相对路径现在固定按 `20-engine` 仓库根目录解析，不受当前 shell cwd 影响

### 4. 执行审计

```bash
# 对曼城执行完整审计（Rodri 缺阵场景）
python main.py audit \
  --team "曼城 (Man City)" \
  --absent Rodri \
  --odds 1.85 3.20 4.50

# 扫描所有档案概览
python main.py scan

# 模拟落后场景 + 禁用 Discord
python main.py audit \
  --team "皇马 (Real Madrid)" \
  --trailing \
  --no-discord
```

## 核心架构

### 动态熵值公式

```
S_dynamic = S_base
          + Fear_Factor_Modifier
          + Injury_Modifier
          + Efficiency_Modifier

其中：
  Fear_Factor_Modifier = Σ(战术维度风险修正) + 比分/语境修正
  Injury_Modifier      = Σ(缺阵关键节点 × 0.40) + Σ(被锁关键节点 × 0.25)
  efficiency_ratio     = xG / (passes_attacking_third + 1.0)

  if efficiency_ratio > 0.08:
      Efficiency_Modifier = -0.15
  elif efficiency_ratio < 0.03 and passes_attacking_third > 50:
      Efficiency_Modifier = +0.10
  else:
      Efficiency_Modifier = 0.0

最终安全约束：
  S_dynamic = clip(S_dynamic, 0.1, 0.9)

当 S_dynamic > threshold（默认 0.7）时 → CRITICAL_WARNING
```

### What-If 三大压力场景

| 场景 | 权重 | 说明 |
|------|------|------|
| A - 核心坍塌 | 50% | 关键节点缺阵/被封时的战术退化 |
| B - 比分高压 | 30% | 落后>60分钟时的防线退化 |
| C - 决策异化 | 20% | 高压下球员决策质量下降 |

### 5x5 战术矩阵标签

| 维度 | 取值 | 含义 |
|------|------|------|
| P（抗压） | P1/P3/P5 | 极强/中等/无抗压 |
| Space（空间） | H/W/C | 全能/边路/中路 |
| F（转换） | F/M/S | 快/被动/慢 |
| H（防线高度） | H/M/L | 高位/中场/低位 |
| Set_Piece（定位球） | A/N/V | 优势/中性/弱势 |

### Obsidian 档案必填 YAML 字段

```yaml
---
version: 4.0
tactical_entropy_base: 0.35      # 基础熵值
system_fragility_threshold: 0.7  # 阈值
key_node_dependency:             # 关键节点名单（列表）
  - Rodri
  - De Bruyne
tactical_logic:                  # 战术矩阵（字典）
  P: P1
  Space: H
  F: S
  H: H
  Set_Piece: A
xG: 1.35                         # 可选：赛后遥测 xG (float)
passes_attacking_third: 72       # 可选：进攻三区成功传球数 (int)
---
```

## 事实门禁规则

- RAG 库无逆境样本 → 强制停机输出 `[Unknown: Insufficient Resilience Data]`
- S 级情报超过 21 天未更新 → 战术标签自动降权
- 比分来源无法核实 → 停止输出，不得捏造

## 环境变量说明

| 变量 | 必填 | 说明 |
|------|------|------|
| `ARES_VAULT_PATH` | 是 | Obsidian Vault 根目录绝对路径 |
| `OPENAI_API_KEY` | 是（LLM推演） | GPT-4o API Key |
| `DISCORD_WEBHOOK_URL` | 否 | Discord Webhook URL |
