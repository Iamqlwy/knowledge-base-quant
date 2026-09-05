import json

ANALYZE_SYSTEM = """你是金融知识图谱分析专家，负责发现实体之间的关系。

## 实体白名单 — 最高优先级，违反此条即视为错误

下方用户消息中的「实体白名单」是本次分析**唯一允许使用**的实体集合。

硬性规则：
- relationships 中每个 source 和 target 必须是白名单中的实体名，不得使用白名单外的任何名称
- 资讯中出现的其他机构、产品、人物、指标、资产，只要不在白名单内，一律忽略
- 即使资讯明确描述了某实体的行为或关系，只要它不在白名单内，就不得将其写入 source/target

反例（白名单=["人民币","美元"]，资讯提到"美联储加息推动美元走强，日元贬值"）：
  ✗ 错误: source="美联储", target="美元"  — "美联储"不在白名单
  ✗ 错误: source="美元", target="日元"    — "日元"不在白名单
  ✓ 正确: 只评估人民币和美元，如果两者之间在资讯中没有直接关系，返回空 relationships

## 关系类型

每条关系有明确的方向 A → B。对称型关系方向无意义。

### 单向关系（A → B）
- impacts: A 影响 B（因果/传导关系）
- regulates: A 对 B 有监管/管辖权力
- sanctions: A 对 B 实施制裁/限制
- holds: A 持有 B（股权、债权、资产、储备）
- part_of: A 是 B 的组成部分
- supplies: A 向 B 供应产品或服务
- produces: A 生产/制造/发布 B

### 对称关系（A ↔ B）
- competes_with: A 与 B 在同一市场竞争
- substitutes: A 与 B 可互相替代
- cooperates_with: A 与 B 合作
- correlated_with: A 与 B 价格/走势联动共振

### 等同
- same_as: A 与 B 是同一实体（仅用于去重，不表示语义关系）

## 规则
- 只提取资讯中明确体现的关系，不编造、不推测
- 无有效关系时返回 {"relationships": []}
- 只返回 JSON，不要任何其他文字

## 关系强度 (strength)

每条关系需标注 strength (0.0~1.0)，综合以下因素：
- 关系确定性：资讯中明确陈述（0.8+） vs 间接推断（0.4-0.6）
- 关系紧密程度：强监管/制裁/持有 > 一般合作/关联
- 资讯中对该关系的着墨程度：重点讨论 vs 一笔带过


## 输出前逐条自检（必须执行）

生成 JSON 之前，对每一条记录执行以下检查：
1. relationships[].source — 是否全部在白名单内？不是则删除该条
2. relationships[].target — 是否全部在白名单内？不是则删除该条
3. relationships[].strength — 是否已赋值且符合上述基准？不是则修正

只输出通过全部检查的 JSON。"""


def build_analyze_prompt(title: str, body: str, entities: list[dict]) -> str:
    entities_json = json.dumps(
        [{"name": e["name"], "entity_type": e["entity_type"]} for e in entities],
        ensure_ascii=False,
    )
    return f"""请发现白名单内实体之间的关系，只返回 JSON：
{{
  "relationships": [
    {{"source": "实体A", "target": "实体B", "relationship_type": "类型", "strength": 0.0, "description": "关系描述"}}
  ]
}}

实体白名单（本次分析唯一允许使用的实体）:
{entities_json}

资讯标题: {title}
资讯内容: {body}"""


class PipelineAgent:
    """封装 LLM 调用为 Pipeline 需要的具体任务。"""

    def __init__(self, llm):
        self.llm = llm

    async def analyze(self, title: str, body: str,
                      entities: list[dict]) -> tuple[list[dict], list[dict]]:
        scores = [
            {"name": e["name"], "entity_type": e["entity_type"],
             "importance_score": e.get("importance", 0.5)}
            for e in entities
        ]
        if len(entities) < 2:
            return scores, []
        user_msg = build_analyze_prompt(title, body, entities)
        result = await self.llm.chat_json(ANALYZE_SYSTEM, user_msg)
        return scores, result.get("relationships", [])
