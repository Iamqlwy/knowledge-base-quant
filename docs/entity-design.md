# 实体类型设计

## 实体（Entity）清单

### 宏观经济与社会

| 类型 | 代码 | 说明 | 实体举例 |
|---|---|---|---|
| 央行 | `central_bank` | 各国央行 | 中国人民银行、美联储、欧央行、日本央行 |
| 经济指标 | `indicator` | 定期发布的宏观数据 | CPI、PPI、PMI、社会融资总额、M2、工业增加值、进出口、GDP |
| 货币 | `currency` | 货币 | CNH、CNY、USD、JPY、EUR、HKD |
| 地区 | `region` | 国家/地区 | 中国、美国、欧盟、日本、香港、台湾、印度 |

### 市场 / 监管 / 政策

| 类型 | 代码 | 说明 | 实体举例 |
|---|---|---|---|
| 政策 | `policy` | 政策方向 | 新质生产力、碳中和、中特估、房住不炒、共同富裕、设备更新 |
| 法规 | `regulation` | 具体法规文件 | 减持新规、再融资办法、数据安全法、私募管理办法 |
| 制度 | `industry_rule` | 交易所/行业协会规则 | 注册制规则、退市新规、ST处理规则 |
| 机构 | `institution` | 持仓能影响市场的机构 | 汇金、社保基金、公募、私募、北向资金、国家队 |
| 指数 | `index` | 市场指数 | 沪深300、科创50、中证500、创业板指、恒生科技、MSCI中国 |

### 产业链

| 类型 | 代码 | 说明 | 实体举例 |
|---|---|---|---|
| 公司 | `company` | 上市公司及关键非上市公司 | 茅台、宁德时代、华为、字节跳动 |
| 行业/板块 | `sector` | 申万行业/Wind板块 | 食品饮料、电力设备、新能源、半导体、医药 |
| 概念/主题 | `concept` | 市场概念 | AI、机器人、可控核聚变、合成生物、铜连接、低空经济 |
| 商品 | `commodity` | 同质化大宗商品 | 铜、锂、碳酸锂、稀土、硅料、螺纹钢、原油 |
| 产品 | `product` | 差异化产品/品牌 | iPhone、华为Mate、司美格鲁肽、PD-1抑制剂、飞天茅台 |

### 事件

| 类型 | 代码 | 说明 | 实体举例 |
|---|---|---|---|
| 事件 | `event` | 固定/预期的事件 | 两会、政治局会议、美联储议息、财报季、MSCI调仓、双十一 |

### 人物

| 类型 | 代码 | 说明 | 实体举例 |
|---|---|---|---|
| 人物 | `person` | 官员/高管/KOL | 鲍威尔、黄仁勋、马斯克、段永平，巴菲特 |

---

## 实体类型汇总

| # | 代码 | 中文 | 归类 |
|---|---|---|---|
| 1 | `central_bank` | 央行 | 宏观·社会 |
| 2 | `indicator` | 经济指标 | 宏观·社会 |
| 3 | `currency` | 货币 | 宏观·社会 |
| 4 | `region` | 国家/地区 | 宏观·社会 |
| 5 | `policy` | 政策方向 | 监管·政策 |
| 6 | `regulation` | 法规文件 | 监管·政策 |
| 7 | `industry_rule` | 制度/规则 | 监管·政策 |
| 8 | `institution` | 机构 | 市场 |
| 9 | `index` | 指数 | 市场 |
| 10 | `company` | 公司 | 产业链 |
| 11 | `sector` | 行业/板块 | 产业链 |
| 12 | `concept` | 概念/主题 | 产业链 |
| 13 | `commodity` | 商品 | 产业链 |
| 14 | `product` | 产品 | 产业链 |
| 15 | `event` | 事件 | 事件 |
| 16 | `person` | 人物 | 人物 |

---

## 实体关系（EntityRelationship）类型

### 产业链关系

| 关系类型 | 代码 | 方向 | 说明 |
|---|---|---|---|
| 供应 | `supplies` | company/region → commodity/product | 供应了什么 |
| 消费 | `consumes` | company/region → commodity/product | 消耗了什么 |
| 上游 | `upstream_of` | company → company | 上游关系 |
| 下游 | `downstream_of` | company → company | 下游关系 |

注：`upstream_of`/`downstream_of` 是**关系类型**，不再作为实体类型。

### 竞争关系

| 关系类型 | 代码 | 方向 | 说明 |
|---|---|---|---|
| 竞争 | `competes_with` | company ↔ company | 互相竞争 |
| 替代 | `substitutes` | product/commodity → product/commodity | 替代关系 |

### 归属关系

| 关系类型 | 代码 | 方向 | 说明 |
|---|---|---|---|
| 属于 | `part_of` | company → sector, company → concept | 归属 |
| 包含 | `contains` | sector → company, concept → company | 逆向归属 |
| 持仓 | `holds` | institution → company / commodity | 机构持有 |

### 影响/驱动关系

| 关系类型 | 代码 | 方向 | 说明 |
|---|---|---|---|
| 影响 | `impacts` | policy → company, indicator → sector, central_bank → currency | 产生影响 |
| 驱动 | `drives` | indicator → sector, policy → sector | 强驱动 |
| 挂钩 | `tracks` | company → index, product → commodity | 跟踪/挂钩 |
| 监管 | `regulates` | central_bank → institution, regulation → company | 监管 |
| 发布 | `publishes` | central_bank → indicator, institution → indicator | 发布数据 |

### 关联关系

| 关系类型 | 代码 | 方向 | 说明 |
|---|---|---|---|
| 关联 | `correlated_with` | indicator ↔ indicator, commodity ↔ commodity | 统计相关性 |
| 相同 | `same_as` | commodity ↔ commodity, product ↔ product | 本质相同 |

---

## 关系类型汇总

| # | 代码 | 所属分类 |
|---|---|---|
| 1 | `supplies` | 产业链 |
| 2 | `consumes` | 产业链 |
| 3 | `upstream_of` | 产业链 |
| 4 | `downstream_of` | 产业链 |
| 5 | `competes_with` | 竞争 |
| 6 | `substitutes` | 竞争 |
| 7 | `part_of` | 归属 |
| 8 | `contains` | 归属 |
| 9 | `holds` | 归属 |
| 10 | `impacts` | 影响驱动 |
| 11 | `drives` | 影响驱动 |
| 12 | `tracks` | 影响驱动 |
| 13 | `regulates` | 影响驱动 |
| 14 | `publishes` | 影响驱动 |
| 15 | `correlated_with` | 关联 |
| 16 | `same_as` | 关联 |

---

## 关键设计决策

### commodity vs product

- **commodity**（商品）：同质化大宗，定价权在供需，不用关心品牌/渠道。分析看库存、产能、边际成本、供需平衡表
- **product**（产品）：差异化竞争品，定价权在品牌/技术/渠道。分析看市占率、ASP、产品周期

### regulation vs policy vs industry_rule

- **policy**（政策方向）：抽象方向性指导（"稳中求进"、"壮大耐心资本"）
- **regulation**（法规）：具体可追溯的文件（《上市公司股东减持股份管理暂行办法》）
- **industry_rule**（制度）：交易所/协会层面的规则（注册制、ST处理、涨跌幅限制）

三者的时效性不同：policy 是最慢变化的底层方向，regulation 几年修订一次，industry_rule 可能随时调整。

### upstream/downstream 从实体层移到关系层

之前实体类型列表里混入了 `upstream`/`downstream`，这是设计错误。上游/下游描述的是两个实体之间的**关系方向**，不是实体的固有属性。移到 `entity_relationship.relationship_type` 后，同一个公司可以同时是：

- 对客户 → upstream_of
- 对供应商 → downstream_of

### event 的用法

event 实体用于挂载预期→实际的对比分析。例如"两会2026"这个 event 节点上可以挂载：
- 会前预期（机构预测政策方向）
- 会后结果（实际出台的政策文本）
- 市场反应（会后3天板块表现）
- 复盘总结（预期偏差分析）
