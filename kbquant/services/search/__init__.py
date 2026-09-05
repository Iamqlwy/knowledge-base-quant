"""搜索流水线子模块

阶段1: entity_resolver  - 实体识别
阶段2: query_rewriter   - 查询改写
阶段3: recall_service   - 多路召回
阶段4: hard_filter      - 硬过滤
阶段5: fusion_service   - RRF 融合
阶段6: rerank_service   - 重排序
阶段7: final_ranking    - 最终排序
阶段7.5: special_rules  - 特殊业务规则（去重、多样化、实体置顶、时效覆盖等）
"""
