# 实体词表扩充计划

## 目的

扩充 `data/entities/` 下的实体词表，提升 Entity Resolver（搜索流水线阶段1）的实体识别覆盖率。

## 可用的开源词库

### 核心来源

| 来源 | 地址 | 范围 | License |
|------|------|------|---------|
| **DomainWordsDict** | `github.com/liuhuanyong/DomainWordsDict` | 68 领域，916 万词，MIT 协议 | MIT |
| **funNLP** | `github.com/fighting41love/funNLP` | 11+ 领域词库（THU整理），66k stars | 免费使用 |
| **搜狗细胞词库** | `pinyin.sogou.com/dict/` | 化学化工、各行业术语 | 公开下载 |

### DomainWordsDict 68 领域完整列表

| 领域 | 词数 | 领域 | 词数 | 领域 | 词数 |
|------|------|------|------|------|------|
| 数学科学 | 17,287 | 纺织服装 | 28,111 | 航空航天 | 682 |
| 环境科学 | 7,891 | 医药医学 | 549,008 | 建筑装潢 | 32,826 |
| 物理科学 | 12,989 | 化学化工 | 40,316 | 汽车行业 | 10,294 |
| 天文科学 | 4,135 | 电力电气 | 50,429 | 船舶工程 | 5,424 |
| 生物动植 | 314,030 | 机械工程 | 9,164 | 材料包装 | 1,473 |
| 地理测绘 | 53,610 | 电子工程 | 6,107 | 矿业勘探 | 20,817 |
| 计算机业 | 55,037 | 通信工程 | 3,814 | 钢铁冶金 | 89,114 |
| 金融财经 | 605,698 | 安全工程 | 4,051 | 农林牧渔 | 38,611 |
| 人力招聘 | 447,606 | 水利工程 | 30,584 | 餐饮食品 | 201,163 |
| 法律诉讼 | 62,717 | 土木工程 | 56,720 | 纺织服装 | 28,111 |
| 组织/机构 | 369,709 | 矿业勘探 | 20,817 | 市场购物 | 63,732 |
| 交通运输 | 27,230 | 航空航天 | 682 | 手机数码 | 10,955 |
| 军事情报 | 76,249 | 管理科学 | 20,751 | 网络游戏 | 522,150 |
| 家居装饰 | 8,668 | 旅游交通 | 52,848 | 网络文学 | 95,331 |
| 美容美发 | 9,662 | 体育/球队 | 48,602 | 期货期权 | 1,300 |

### funNLP 领域词库

| 词库 | 内容 |
|------|------|
| IT词库 | 信息技术领域术语 |
| 财经词库 | 金融/证券/经济术语 |
| 医学词库 | 医药/临床/中医术语 |
| 法律词库 | 法学/法条/罪名术语 |
| 汽车品牌词库 | 全球汽车品牌名 |
| 汽车零件词库 | 汽车配件/系统术语 |
| 公司名字大全 | 中国公司名称 |
| 饮食词库 | 食品/饮品/菜系 |
| 动物词库 | 动物学名/俗名 |
| 地名词库 | 国内外地名 |
| 历史名人词库 | 历史人物姓名 |
| 同义词/反义词库 | 中文近义/反义词 |

## 现有资产与待补充对照

| 现有文件 | 现状 | 待建词表 | 可用的开源来源 |
|----------|------|---------|--------------|
| company.json | 5,497 家 A股，aliases 不全 | 曾用名/简称/俗称补充 | AkShare / Tushare |
| sector.json | 行业板块，无成分股关联 | 行业→成分股映射 | 东方财富API / AkShare |
| concept.json | 315 个A股概念 | 覆盖尚可 | 不需要大幅扩充 |
| commodity.json | 大宗商品品种 | aliases 补充（如 LPG→液化石油气） | 期货品种手册 |
| **无** | **缺少** | **strategy.json**（策略/交易术语） | funNLP 财经词库 + 手动整理 |
| **无** | **缺少** | **scientific_term.json**（科技/科学术语） | DomainWordsDict（物理/电子/通信/计算机/数学等） |
| **无** | **缺少** | **chemical_term.json**（化工/原材料术语） | DomainWordsDict + 搜狗化工词库 |
| **无** | **缺少** | **tech_company.json**（全球科技企业） | funNLP IT词库 + 手动整理 |
| **无** | **缺少** | **medical_term.json**（医药术语） | DomainWordsDict 医药医学(549k) + funNLP 医学词库 |
| **无** | **缺少** | **military_term.json**（军事术语） | DomainWordsDict 军事情报(76k) |
| **无** | **缺少** | **automotive_term.json**（汽车术语） | DomainWordsDict 汽车行业(10k) + funNLP 汽车品牌/零件 |
| **无** | **缺少** | **food_product.json**（食品饮料品牌） | DomainWordsDict 餐饮食品(201k) + funNLP 饮食词库 |
| **无** | **缺少** | **textile_term.json**（纺织服装术语） | DomainWordsDict 纺织服装(28k) |
| **无** | **缺少** | **pharma_biotech.json**（医药/生物技术） | DomainWordsDict 医药医学(549k) + 手动整理GLP-1/ADC/CAR-T等前沿术语 |
| **无** | **缺少** | **energy_term.json**（新能源术语） | DomainWordsDict 电力电气(50k) + 手动整理TOPCon/HJT/固态电池等 |
| **无** | **缺少** | **semiconductor_term.json**（半导体术语） | DomainWordsDict 计算机/电子 + 手动整理HBM/CoWoS/GAA/EUV等 |
| **无** | **缺少** | **telecom_term.json**（通信术语） | DomainWordsDict 通信工程(3.8k) + 手动整理5G-A/6G/卫星互联网等 |
| **无** | **缺少** | **fund.json**（基金/ETF） | 天天基金/AkShare |
| person.json | 少量 | 大幅扩充（科技/金融/政策人物+知名人物） | funNLP 历史名人词库 + 手动整理 |
| product.json | 已有大量 | 补充品牌/消费品牌 | DomainWordsDict 市场购物(63k) + 家居装饰(8.6k) + 手机数码(10k) |
| institution.json | 已有大量 | 补充全球机构 | DomainWordsDict 组织机构(369k) |

## 优先级

```
P0（本周阻塞）:
  strategy.json          — 策略术语（影响 feedback 表召回）
  scientific_term.json   — 科技/科学术语（物理/材料/电子/通信/计算机）
  chemical_term.json     — 化工/原材料术语
  semiconductor_term.json — 半导体术语（高频搜索领域）
  energy_term.json       — 新能源术语（钙钛矿/TOPCon/HJT/固态电池等）
  pharma_biotech.json    — 医药/生物技术术语
  tech_company.json      — 全球科技企业（funNLP IT词库 + 手动补充）
  person.json 补充        — 科技/金融/政策人物 + funNLP 历史名人词库

P1（下周）:
  medical_term.json      — 医药术语（DomainWordsDict 549k）
  military_term.json     — 军事/武器装备术语（DomainWordsDict 76k）
  automotive_term.json   — 汽车术语（funNLP 汽车品牌+零件 + DomainWordsDict）
  food_product.json      — 食品饮料品牌（DomainWordsDict 201k）
  textile_term.json      — 纺织服装术语（DomainWordsDict 28k）
  telecom_term.json      — 通信术语（DomainWordsDict 3.8k + 手动补 5G/6G）
  company.json aliases   — AkShare 补全

P2（后续）:
  fund.json              — 基金/ETF（天天基金/AkShare）
  commodity.json aliases — 期货品种补充
```

## 来源汇总

| 词表 | 来源 | License |
|------|------|---------|
| 科学技术术语 | DomainWordsDict (物理/电子/通信/计算机/数学/材料等) | MIT |
| 化工术语 | DomainWordsDict 化学化工 + 搜狗细胞词库(化工) | MIT / 公开下载 |
| 医药术语 | DomainWordsDict 医药医学(549k) + funNLP 医学词库 | MIT / 免费 |
| 半导体术语 | DomainWordsDict(计算机/电子) + 手动补充 | MIT |
| 新能源术语 | DomainWordsDict(电力电气) + 手动补充 | MIT |
| 纺织术语 | DomainWordsDict 纺织服装 | MIT |
| 汽车术语 | DomainWordsDict 汽车行业 + funNLP 汽车品牌/零件 | MIT / 免费 |
| 食品术语 | DomainWordsDict 餐饮食品 + funNLP 饮食词库 | MIT / 免费 |
| 军事术语 | DomainWordsDict 军事情报(76k) | MIT |
| 通信术语 | DomainWordsDict 通信工程 + 手动补充 | MIT |
| 机构/公司 | DomainWordsDict 组织机构(369k) + funNLP 公司名字大全 | MIT / 免费 |
| 人物 | funNLP 历史名人词库 + 手动补充 | 免费 |
| 基金/ETF | AkShare API | MIT |
| 股票aliases | AkShare / Tushare | MIT |
| 策略术语 | funNLP 财经词库 + 手动整理 | 免费 |

## 补充来源（具体说明）

### 股票 aliases 补充（company.json）

**来源**：AkShare `stock_info_a_code_name()` 或 Tushare `stock_basic`

**需要补充**：历史上用过的名称、常用简称/俗称、知名 act_name（实际控制人）

### 行业成分股关联（sector.json）

**来源**：东方财富行业板块 API 或 AkShare `stock_board_concept_cons_ths`

**需要补充**：行业 → 成分股列表，让搜索"白酒板块"时即使文档标题不含"白酒"也能通过贵州茅台、五粮液等召回

### 策略术语（新增 strategy.json）

**来源**：funNLP 财经词库 + 手动整理

打板/止损/技术分析/资金面术语。格式：
```json
{"name": "炸板", "entity_type": "strategy_term", "aliases": ["封板失败", "开板"], "metadata": {"domain": "打板策略"}}
```

### 科技/科学术语（新增 scientific_term.json）

**来源**：DomainWordsDict（MIT协议）

从物理科学、电子工程、通信工程、计算机业、数学科学、材料包装、环境科学、天文科学等领域提取。格式：
```json
{"name": "钙钛矿", "entity_type": "scientific_term", "aliases": ["Perovskite", "钙钛矿光伏"], "metadata": {"domain": "新能源/材料"}}
```

### 化工术语（新增 chemical_term.json）

**来源**：DomainWordsDict 化学化工(40k) + 搜狗细胞词库(化学化工词汇大全，公开下载) + Global-Chem 知识图谱

覆盖大宗原料、石化、氟化工/硅化工、颜料/涂料、合成材料、电子化学品、锂电材料、有色金属、光伏材料。格式：
```json
{"name": "MDI", "entity_type": "chemical_term", "aliases": ["二苯基甲烷二异氰酸酯", "聚合MDI"], "metadata": {"domain": "化工/聚氨酯"}}
```

### 半导体术语（新增 semiconductor_term.json）

**来源**：DomainWordsDict(计算机/电子) + 手动补充前沿术语

EUV光刻、FinFET、GAA、HBM、CoWoS、Chiplet、SiC、GaN、SoC、FPGA、ASIC、DDR5、LPDDR5X、NAND Flash、HBM4等。格式：
```json
{"name": "HBM", "entity_type": "semiconductor_term", "aliases": ["高带宽内存", "HBM3", "HBM4"], "metadata": {"domain": "半导体/存储"}}
```

### 新能源术语（新增 energy_term.json）

**来源**：DomainWordsDict 电力电气(50k) + 手动补充前沿术语

钙钛矿、TOPCon、HJT、BC电池、固态电池、钠离子电池、LFP、NCM、刀片电池、4680、液冷、浸没式冷却、PUE等。格式：
```json
{"name": "TOPCon", "entity_type": "energy_term", "aliases": ["隧穿氧化层钝化接触"], "metadata": {"domain": "新能源/光伏电池"}}
```

### 医药/生物技术术语（新增 pharma_biotech.json）

**来源**：DomainWordsDict 医药医学(549k) + funNLP 医学词库 + 手动补充

GLP-1、ADC、CAR-T、mRNA、CRISPR、siRNA、PROTAC、双抗、偶联药物、基因编辑、合成生物学、细胞治疗等。格式：
```json
{"name": "GLP-1", "entity_type": "pharma_term", "aliases": ["胰高血糖素样肽-1", "司美格鲁肽", "替西帕肽"], "metadata": {"domain": "医药/代谢疾病"}}
```

### 全球科技企业（新增 tech_company.json）

**来源**：funNLP IT词库 + 手动整理

全球科技巨头、中国科技企业、AI大模型公司、半导体企业、人形机器人企业、知名独角兽。格式：
```json
{"name": "NVIDIA", "entity_type": "tech_company", "aliases": ["英伟达", "NVDA"], "metadata": {"sector": "半导体", "market": "NASDAQ"}}
```

### 人物扩充（更新 person.json）

**来源**：funNLP 历史名人词库 + 手动补充

全球科技领袖、中国科技领袖、金融/投资界、经济学家/央行、政策制定者、AI学术领袖。格式：
```json
{"name": "黄仁勋", "entity_type": "person", "aliases": ["Jensen Huang", "老黄"], "metadata": {"role": "NVIDIA创始人兼CEO"}}
```

### 其他行业词表（P1/P2）

| 词表 | 领域 | 来源 | 词数 |
|------|------|------|------|
| medical_term.json | 医药 | DomainWordsDict 医药医学 + funNLP 医学 | 549k |
| military_term.json | 军事 | DomainWordsDict 军事情报 | 76k |
| automotive_term.json | 汽车 | DomainWordsDict 汽车行业 + funNLP 品牌/零件 | 10k+ |
| food_product.json | 食品 | DomainWordsDict 餐饮食品 + funNLP 饮食 | 201k |
| textile_term.json | 纺织 | DomainWordsDict 纺织服装 | 28k |
| telecom_term.json | 通信 | DomainWordsDict 通信工程 + 手动补充 | 3.8k+ |

## 实现方式

写一个 `scripts/update_entities.py`，支持：
- `--source akshare`：通过 AkShare 拉取数据
- `--dry-run`：对比现有，只输出变更清单
- `--merge`：合并到现有 JSON 文件

## 进度跟踪

- [ ] strategy.json 新建（策略术语）
- [ ] scientific_term.json 新建（科技/科学术语）
- [ ] chemical_term.json 新建（化工/原材料术语）
- [ ] tech_company.json 新建（科技企业）
- [ ] person.json 大幅扩充（科技/金融/政策人物）
- [ ] company.json aliases 补全（曾用名/简称）
- [ ] sector.json 关联成分股
- [ ] fund.json 新建
- [ ] commodity.json aliases 补全
