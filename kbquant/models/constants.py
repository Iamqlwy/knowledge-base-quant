from enum import StrEnum


class EntityType(StrEnum):
    """实体类型 — 16 类"""
    # 宏观·社会
    CENTRAL_BANK = "central_bank"
    INDICATOR = "indicator"
    CURRENCY = "currency"
    REGION = "region"
    # 监管·政策
    POLICY = "policy"
    REGULATION = "regulation"
    INDUSTRY_RULE = "industry_rule"
    # 市场
    INSTITUTION = "institution"
    INDEX = "index"
    # 产业链
    COMPANY = "company"
    SECTOR = "sector"
    CONCEPT = "concept"
    COMMODITY = "commodity"
    PRODUCT = "product"
    # 事件
    EVENT = "event"
    # 人物
    PERSON = "person"


class RelationshipType(StrEnum):
    """实体关系类型 — 12 类

    覆盖金融资讯中所有实体类型组合的典型关系：
    - 单向关系：反方向通过反向遍历图获取
    - 对称关系：A↔B 方向无意义，双向等价
    """

    # === 单向关系 ===
    IMPACTS = "impacts"               # A 影响 B。覆盖面最广：政策→市场、事件→资产、财报→股价、人物讲话→汇率、指标→板块
    REGULATES = "regulates"           # A 监管/管辖 B。央行→商行、证监会→上市公司、发改委→行业
    SANCTIONS = "sanctions"           # A 制裁/限制 B。经济制裁、出口管制、实体清单。区别于 regulates 的惩罚性
    HOLDS = "holds"                   # A 持有 B。持股、持债、储备资产、主权基金持仓
    PART_OF = "part_of"               # A 属于 B。子公司→母公司、成分股→指数、部门→机构、人物→所属组织
    SUPPLIES = "supplies"             # A 供应 B。供应商→客户、上游→下游，覆盖全产业链的货物/服务流动
    PRODUCES = "produces"             # A 生产/制造/发布 B。公司→产品、国家→商品、机构→指标/政策/法规

    # === 对称关系 ===
    COMPETES_WITH = "competes_with"   # A 与 B 竞争。公司↔公司、产品↔产品、国家↔国家
    SUBSTITUTES = "substitutes"       # A 与 B 可互相替代。商品↔商品（豆油↔棕榈油）、技术↔技术（EV↔燃油车）
    COOPERATES_WITH = "cooperates_with"  # A 与 B 合作。公司↔公司（合资/联合研发）、国家↔国家（贸易协定）、机构↔机构
    CORRELATED_WITH = "correlated_with"  # A 与 B 联动共振。美元↔黄金、原油↔化工、利率↔债券、指数↔指数

    # === 等同 ===
    SAME_AS = "same_as"               # A 与 B 指向同一实体。"PBOC" ≡ "中国人民银行"、"Fed" ≡ "美联储"


class WorldNodeEdgeType(StrEnum):
    """世界节点层级关系类型 —— WorldNodeEdge 专用

    区别于 RelationshipType（实体间关系），这里的边用于构建知识图谱 Layer 1 的
    概念层级结构。Agent 从资讯中提取宏观→板块→公司→产品→政策等多维关联。
    """

    # === 层级归属（树形主干） ===
    BELONGS_TO = "belongs_to"
    """A 归属 B。最泛用的层级关系：公司→板块、板块→宏观主题、产品→行业。
    例：特斯拉 belongs_to 新能源车、动力电池 belongs_to 新能源"""

    CLASSIFIED_AS = "classified_as"
    """A 归类为 B。概念/主题归类，比 belongs_to 更松散的非唯一归属。
    例：AI制药 classified_as 医疗AI、钠离子电池 classified_as 下一代电池技术"""

    # === 业务构成 ===
    OPERATES_IN = "operates_in"
    """A 在 B 领域有主营业务。多业务公司 → 每个业务线对应的行业/板块。
    例：比亚迪 operates_in 新能源汽车、比亚迪 operates_in 消费电子代工、比亚迪 operates_in 光伏"""

    HAS_BUSINESS_SEGMENT = "has_business_segment"
    """A 拥有 B 业务板块。反方向于 operates_in，父节点视角。
    例：比亚迪 has_business_segment 新能源汽车业务"""

    DERIVES_REVENUE_FROM = "derives_revenue_from"
    """A 的收入来源于 B。用于建模公司→产品→终端市场的收入依赖链。
    例：宁德时代 derives_revenue_from 动力电池、宁德时代 derives_revenue_from 储能系统"""

    # === 供应链 ===
    UPSTREAM_OF = "upstream_of"
    """A 是 B 的上游。供应商→客户方向，覆盖原材料→零部件→成品→品牌链条。
    例：台积电 upstream_of 英伟达、锂矿 upstream_of 正极材料 upstream_of 动力电池"""

    DOWNSTREAM_OF = "downstream_of"
    """A 是 B 的下游。客户→供应商方向，与 upstream_of 互为反向。
    例：特斯拉 downstream_of 宁德时代、苹果 downstream_of 富士康"""

    # === 竞争与替代 ===
    COMPETES_IN = "competes_in"
    """A 与 B 在同一赛道竞争。区别于 competes_with（实体关系），这里用于
    将多家公司挂到同一个竞争赛道节点下。
    例：比亚迪 competes_in 新能源汽车赛道、特斯拉 competes_in 新能源汽车赛道"""

    THREATENS = "threatens"
    """A 的产品/技术对 B 构成颠覆性威胁。用于建模新旧技术迭代关系。
    例：固态电池 threatens 液态锂电池、电动汽车 threatens 传统燃油车"""

    # === 政策与监管 ===
    REGULATED_BY = "regulated_by"
    """A 受 B 政策/法规的监管约束。
    例：加密货币 regulated_by 金融监管政策、光伏 regulated_by 新能源补贴政策"""

    BENEFITS_FROM = "benefits_from"
    """A 受益于 B 政策/补贴/趋势。比 regulated_by 更积极，表示政策利好。
    例：新能源汽车 benefits_from 碳中和政策、国产芯片 benefits_from 国产替代政策"""

    CONSTRAINED_BY = "constrained_by"
    """A 受 B 政策/外部因素的制约。与 benefits_from 相反，表示政策利空或限制。
    例：房地产 constrained_by 三条红线、教培 constrained_by 双减政策"""

    # === 事件驱动 ===
    AFFECTED_BY = "affected_by"
    """A 受 B 事件/宏观变量的影响。比 impacts 更聚焦于节点对事件因子的暴露。
    例：航空公司 affected_by 原油价格、出口型企业 affected_by 人民币汇率"""

    DRIVEN_BY = "driven_by"
    """A 的核心驱动力来自 B。主动/正面的因果关系线，用于建模投资主线的传导链。
    例：AI算力 driven_by 大模型军备竞赛、黄金 driven_by 全球去美元化"""

    # === 地域 ===
    BASED_IN = "based_in"
    """A 的注册地/总部所在地/主要市场在 B。公司→国家/地区。
    例：台积电 based_in 中国台湾、三星电子 based_in 韩国"""

    EXPOSED_TO = "exposed_to"
    """A 对 B 地域有业务敞口。公司→非总部的地域市场。
    例：苹果 exposed_to 中国市场、力拓 exposed_to 澳大利亚矿业政策"""

    # === 人物与机构 ===
    LED_BY = "led_by"
    """A 由 B 人物领导/关键决策。
    例：特斯拉 led_by Elon Musk、英伟达 led_by 黄仁勋"""

    AFFILIATED_WITH = "affiliated_with"
    """A 与 B 机构有附属/关联关系。人物→机构、子公司→母公司。
    例：某基金经理 affiliated_with 高瓴资本"""
