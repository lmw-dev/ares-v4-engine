---
tags:
  - project/ares-engine
  - type/prd
  - ares/v4-agent
status: active
version: 1.0
creation_date: 2026-04-15
owner: Ares
related: "[[体系 - Ares Football 深度分析与 RAG 对冲系统 v4.0]]"
---

# PRD - Ares v4.0 动态战术压力审计引擎

> [!abstract] 文档定位
> 本 PRD 旨在指导 AI 编程助手（如 Codex, Cursor）从零初始化 `ares-v4-engine` Python 后端项目。该引擎作为 OpenClaw 的专职数据节点，负责读取物理隔离的 Obsidian 战术数据库，执行 RAG 压力推演，并输出赛前博弈决策。

## 1. 核心架构与技术栈
- **设计哲学**：代码与数据绝对物理隔离（Decoupled Architecture）。
- **运行环境**：Python 3.10+
- **关键依赖 (`requirements.txt`)**：
    - `python-dotenv`: 环境变量管理（路径解耦）。
    - `PyYAML`, `python-frontmatter`: 解析 Obsidian Markdown 档案。
    - `chromadb`: 本地轻量级向量数据库（RAG 引擎）。
    - `openai`: 调用 GPT-4o 进行 What-If 逻辑推演。
    - `watchdog`: 本地文件系统监听（可选）。

## 2. 项目目录规范 (Git Repo)
```text
ares-v4-engine/
├── .env                # 环境变量配置文件（切勿提交入库）
├── .gitignore          # 忽略 .env, __pycache__, venv/, chromadb/
├── requirements.txt    # 依赖清单
├── main.py             # 核心调度器（CLI 入口）
└── src/
    ├── data/           
    │   └── obsidian.py # Obsidian Markdown 档案解析器
    ├── engine/         
    │   ├── entropy.py  # 动态熵值 (S值) 算法内核
    │   └── simulator.py# 基于 ChromaDB 的 RAG 压力测试器
    ├── integrations/   
    │   ├── market.py   # 赔率对冲与 EV 剪刀差计算
    │   └── delivery.py # 结果格式化与 Discord Webhook 投递
    └── utils/          
        └── logger.py   # 终端美化与执行日志
````

## 3. 核心模块与功能需求

### 3.1 数据挂载层 (`src/data/obsidian.py`)

- **前置条件**：读取 `.env` 中的 `ARES_VAULT_PATH`（指向外部 Obsidian Vault 的绝对路径）。
    
- **执行逻辑**：
    
    1. 递归扫描该路径下的 `02_Team_Archives` 子目录（需支持读取 `Premier_League` 等嵌套文件夹）。
        
    2. 使用 `python-frontmatter` 提取 YAML 头部。
        
    3. 提取必须包含且结构完整的字段：`version`, `tactical_entropy_base`, `key_node_dependency` (List), `tactical_logic` (Dict)。
        
    4. 添加异常处理（`FileNotFoundError`, YAML 格式错误等）。
        

### 3.2 逻辑内核层 (`src/engine/entropy.py`)

- **执行逻辑**：接收 `obsidian.py` 传入的参数，计算 $S_{dynamic}$。
    
- **参数权重**：
    
    - 基础值 = `tactical_entropy_base`。
        
    - 若 RAG 库或外部情报确认 `key_node_dependency`（如 Rodri）缺阵或被锁死，熵值需剧增（如 +0.4）。
        
- **阈值熔断**：当最终 $S > 0.7$ 时，标记 `status = 'CRITICAL_WARNING'`。
    

### 3.3 压力测试层 (`src/engine/simulator.py`)

- **执行逻辑**：
    
    1. 接收 `key_node_dependency` 的具体名单。
        
    2. 针对名单构建检索 Query（例如：“球队在失去该节点时的战术运转表现与失误”）。
        
    3. 在 ChromaDB 中进行相似度检索，提取 Top-3 历史文本。
        
    4. 将提取的事实组装成 What-If Prompt，调用 LLM 评估防线退化程度。
        

## 4. 输入数据标准格式 (Data Schema)

AI 助手在编写解析逻辑时，必须确保能完美解析以下格式的外部 Markdown 文件：

YAML

```
---
version: 4.0
tactical_entropy_base: 0.35
system_fragility_threshold: 0.7
key_node_dependency:
  - Rodri
  - De Bruyne
tactical_logic:
  P: P1
  Space: H
  F: S
  H: H
  Set_Piece: A
---
# 此处为 Markdown 正文内容...
```

---
