import re

# All 110 sector nodes with IDs, names, and full descriptions from the data file
sectors = [
    ("18dcaa63-9ee6-4e14-8011-c1a88640a851", "5G板块", "A股5G通信板块，涵盖通信设备商、光模块、基站天线、射频器件等5G产业链相关公司。"),
    ("36ca118c-7c13-442d-85fa-bfb2527038e1", "AI大模型板块", "中国AI大模型行业，涵盖百度文心、阿里Qwen、字节Seed、腾讯混元、DeepSeek、月之暗面等主要玩家。"),
    ("4986f833-452f-49e6-b096-dce7c2b8b845", "AI服务器产业链", "涵盖AI服务器研发设计、核心零部件、整机制造、系统集成等全链条环节。"),
    ("eccff070-0f11-4911-8a52-9a3017ba0daf", "AI算力板块", "涵盖AI芯片、AI服务器、液冷散热、算力租赁、光通信等AI算力基础设施相关公司。"),
    ("50b8d9ec-c6e3-40a8-b2c7-876b660c5fa0", "A股AI应用板块", "A股AI应用概念板块，涵盖AI+办公、AI+营销、AI+游戏、AI+教育等方向。"),
    ("d67972db-c98e-4663-98c2-ed2ef3c1a38a", "CIS图像传感器板块", "CMOS图像传感器（CIS）行业。"),
    ("63d11526-9d6d-49fd-8ee4-4639b20361b5", "CXO板块", "医药外包服务板块，包括CRO和CDMO。"),
    ("7b575664-5840-4807-bbda-d48327dca2e6", "MLCC板块", "片式多层陶瓷电容器（MLCC）行业板块。"),
    ("466fa8ec-6bda-4dbb-8507-fb34c02eb62c", "PCB板块", "印制电路板（PCB）板块。"),
    ("44d3ec68-a276-4f10-b42f-0a6ad66c5309", "中药板块", "A股中药行业板块。"),
    ("571c33a1-2225-4b53-9cef-99736f1f0990", "乳制品板块", "A股/港股乳制品行业板块，涵盖伊利、蒙牛等。"),
    ("e84e0f28-8c89-4c8f-ad2e-1a135fb105b9", "人工智能板块", "A股人工智能板块，涵盖AI大模型、AI应用、AI算力等方向。"),
    ("4f48f977-cd25-4231-8087-4cd3d428e6ca", "人形机器人板块", "A股人形机器人概念板块。"),
    ("8877a450-055e-436b-8968-943b1ee48a34", "体育用品板块", "中国体育用品行业板块。"),
    ("74a8d273-6c6f-4dc2-ac21-2bae24d30008", "保险板块", "A股保险板块。"),
    ("39a694ea-4ca2-4b89-8867-29925850141f", "储能板块", "A股储能相关上市公司板块。"),
    ("22cc3200-0f15-4d6e-b090-476e74b263cb", "元宇宙板块", "涵盖元宇宙产业链相关A股上市公司。"),
    ("7122cd8b-6dfd-4304-8b23-b8599204aa1b", "先进封装板块", "涵盖CoWoS、HBM、Chiplet、混合键合等先进封装技术。"),
    ("a0c78dc2-7926-45b4-98ff-4bfb39f08132", "光伏", "光伏发电行业。"),
    ("a3b14d45-79bc-4d18-b52d-0fdcb48d8ab7", "光伏行业", "光伏行业，涵盖多晶硅、硅片、电池片、组件及光伏设备等产业链环节。"),
    ("16896b05-c458-4f07-975b-9d99954b76cc", "光模块板块", "A股光模块板块。"),
    ("fce37320-3662-4c2a-b94c-2eb04bce6bc4", "光通信板块", "光通信板块，涵盖光模块、光芯片、光纤光缆等。"),
    ("3da1cf20-d90d-418a-9e6f-436ed4998ce3", "全球航空板块", "全球航空运输业。"),
    ("b5461ca7-0178-4c58-8f8f-478310f6442a", "公募基金行业", "公募基金行业。"),
    ("ced4b5f4-8df7-4836-9b91-506f162531fa", "具身智能板块", "具身智能（Embodied AI）板块，涵盖人形机器人、空间感知等。"),
    ("24a5b837-e744-4ca4-84df-e13755b96f88", "军工板块", "A股军工板块。"),
    ("45adc70a-69f6-46db-9c77-99cd71ec5c6b", "创新药板块", "A股及港股创新药研发企业板块。"),
    ("7a7d559e-8dec-46ec-b4b9-5020e9dd0f5e", "券商板块", "A股证券行业板块。"),
    ("dbe0dcc6-cdd0-440c-b129-fa6e4512ade3", "化妆品板块", "A股美容护理/化妆品板块。"),
    ("ae00f110-eafc-43ce-b812-4eae04fe7af5", "化工板块", "A股化工行业板块。"),
    ("dca2cef5-cadb-41b8-89ca-2e256db75826", "化纤板块", "A股化学纤维板块。"),
    ("1e4fd148-4fb9-4be3-81ed-c7293a3f5cb3", "化肥板块", "A股化肥板块。"),
    ("79b01d20-c4fa-47ba-909d-86ed1abb49be", "医疗器械板块", "A股医疗器械行业板块。"),
    ("1f3bca26-15dd-4287-ae7b-57d2be7d6f83", "原料药板块", "化学原料药/医药中间体行业板块。"),
    ("37eff189-470a-4307-9b0d-d8bab9c02d70", "变压器板块", "A股变压器行业板块。"),
    ("df778bad-e208-4c00-b130-ee8897a85158", "可转债板块", "A股可转债市场整体板块。"),
    ("0fa532ed-b61d-4c7d-ba3b-77609577ef43", "商业航天", "商业航天板块，涵盖卫星制造、火箭发射等。"),
    ("e05d8598-dcf9-41a1-a6e2-630f000f12ce", "在线旅游板块", "在线旅游服务平台行业（OTA）。"),
    ("b88b2f27-63f0-4578-9e36-9a7b48efaa58", "外卖平台服务行业", "外卖平台服务行业。"),
    ("a96f22cf-662d-412a-9041-67a61f6af433", "天然橡胶", "天然橡胶行业。"),
    ("e812a1b1-e128-4a75-8ba2-69b945e6f491", "存储芯片板块", "A股存储芯片板块，涵盖DRAM、NAND、NOR Flash等。"),
    ("1b49e1c4-02d9-41f7-97b2-72aced9f9135", "快递物流板块", "A股快递物流行业板块。"),
    ("85d066e7-940f-4033-b086-3d9d06302671", "房地产板块", "A股房地产行业板块。"),
    ("19a80f1e-f8c3-4184-a683-6a64abb34b9b", "数据要素板块", "数据要素板块。"),
    ("d8280c1f-d9ee-4b47-a8bb-b418f2d850d5", "新能源汽车板块", "A股新能源汽车产业链板块。"),
    ("64d958d2-b712-4974-8e85-73e8b2c1c55e", "新茶饮板块", "港股新茶饮上市公司板块。"),
    ("7185f618-91d4-4ddb-863e-0c6a44d6d95e", "明胶板块", "明胶行业。"),
    ("5718c40e-fa23-4eea-90bd-a1ea38876179", "显示面板", "显示面板行业（LCD/OLED）。"),
    ("4daaed79-a355-4dcd-9acc-d6fa1a600a53", "智能制造", "AI+制造相关领域。"),
    ("2896853a-8177-45d3-bf5a-7a8251b0aa25", "智能家居", "智能家居/全屋智能行业。"),
    ("9fb7c6f2-d449-4593-8896-dc4a14005097", "智能网联汽车板块", "智能网联汽车/自动驾驶/车路协同。"),
    ("b4140add-be77-454d-aa43-697a477286f8", "有机硅板块", "A股有机硅概念板块。"),
    ("911c397f-9d9a-4992-a838-33b34c9b4180", "有色金属板块", "A股有色金属行业板块。"),
    ("59535b88-1bb1-459b-b11c-7c762abb0901", "核电板块", "A股核电行业板块。"),
    ("6542a4dd-31f8-4473-8345-59a5b2691644", "检测检验板块", "A股检测检验服务板块。"),
    ("581be628-4d23-4ebf-bbcd-59dc6ca0f6d6", "棉花板块", "A股棉花相关板块。"),
    ("c8ad46e0-7cd2-4011-952b-bd2e4dca6f8c", "氢能板块", "A股氢能产业链板块。"),
    ("db017d62-6050-42fc-95ac-94e03c7d96b6", "水泥板块", "A股水泥行业板块。"),
    ("d58246f7-499e-4bb2-b768-3a627c9fa8f8", "汽车行业", "中国汽车行业。"),
    ("d9177a91-21a8-44fa-9e17-04ac9b750887", "汽车零部件板块", "A股汽车零部件上市公司板块。"),
    ("dcb6c013-0ad0-48cb-bccb-0e9628a1fc92", "油田服务板块", "全球及A股油田服务行业板块。"),
    ("bc761da5-4ef4-40d7-a639-bc4f2b6e6cdd", "浮法玻璃板块", "A股浮法玻璃行业板块。"),
    ("e7b29bf6-9b0f-4b23-9789-46879a92f42e", "海上风电板块", "海上风电板块。"),
    ("49d464da-cd20-43ce-a3de-f1e179fe21c3", "海工装备板块", "A股海工装备概念板块。"),
    ("f677cedc-01cc-4037-9514-90486a6cb889", "涂料板块", "A股涂料行业板块。"),
    ("60daeffc-0a50-42fd-b2cc-de7939e36f33", "消费电子板块", "消费电子行业板块。"),
    ("94fe9e82-9221-422e-a4b3-9ea6ce9e3146", "液冷板块", "数据中心液冷散热板块。"),
    ("78cafef9-9abc-4641-8e01-93b1b0f2da4c", "游戏板块", "A股游戏行业板块。"),
    ("82575dd5-ca58-47e1-b5c3-0cf4be1a5a96", "潮玩板块", "潮流与收藏玩具行业（含盲盒）。"),
    ("b88e1965-8e52-4758-bd37-de937d35d5b9", "火电板块", "A股火力发电行业板块。"),
    ("cf0e50eb-65bf-41c2-9b1c-1031ce70502d", "火锅产业", "火锅餐饮连锁及产业链。"),
    ("97fe736b-40e4-49b6-8f1b-0c8733f24768", "煤炭板块", "A股煤炭开采加工板块。"),
    ("5a591ed6-dada-492a-aad2-21c4d060d2fa", "燃气板块", "A股及港股城市燃气/天然气分销板块。"),
    ("46be6c50-2ae0-4a64-8481-cab92d2f9882", "物联网板块", "A股物联网板块。"),
    ("f1446cd3-f096-41a8-9ec6-fdd4eccd0a90", "特高压板块", "特高压（UHV）输电板块。"),
    ("2bcd71c0-303d-4d81-bcf2-0398810a0a5f", "生猪养殖板块", "A股生猪养殖行业板块。"),
    ("a2e079b0-1bb7-40c8-a070-4e9a375713eb", "电力板块", "A股电力行业板块（火/水/核/风/光）。"),
    ("3cbf9497-6186-4acd-bed3-4f0ea6b79a72", "电动自行车板块", "电动自行车（电动两轮车）行业板块。"),
    ("b3afc0c9-381e-48e8-9842-5abc6cbf679d", "电商代运营板块", "电商代运营行业。"),
    ("3dd7eed6-9872-44e2-9737-0ae5130a3dfd", "电解铝板块", "A股电解铝及铝加工板块。"),
    ("a90c7d93-9b39-4c6d-9a39-b074e7f21242", "白酒板块", "A股及港股白酒行业。"),
    ("35a1562e-b8d0-4c36-bbb4-044b864250e9", "短剧行业", "微短剧/真人短剧/AI短剧行业。"),
    ("649430b3-22b1-486d-a5b2-aa070daa1256", "石油石化板块", "石油天然气勘探开采、炼化、销售全产业链。"),
    ("8ab63a83-79e6-4591-ac7b-a4361c72455e", "碳交易", "碳排放权交易市场板块。"),
    ("3579f50b-5760-4ee7-ae66-0bcecb70d285", "碳化硅板块", "碳化硅（SiC）功率半导体板块。"),
    ("865f3977-7896-4330-a2bb-1368454fbcf8", "碳纤维板块", "碳纤维及复合材料板块。"),
    ("9b4744f3-0682-4704-9a4e-cf12bf4606a0", "碳酸锂板块", "碳酸锂是锂离子电池核心原材料。"),
    ("0cebb21f-fcdd-4124-bd7e-f6eb4a940b12", "种业板块", "A股种业相关上市公司板块。"),
    ("c579ef4d-1610-4011-a096-02a5e52741bc", "空天信息板块", "北斗导航、卫星互联网、遥感与商业航天。"),
    ("521c0bd1-eaf7-4c93-a09a-bc68aea2efad", "纺织板块", "A股纺织板块。"),
    ("7e89c320-729d-4aae-9c45-80998840170d", "美股软件板块", "美股软件/SaaS板块。"),
    ("5e4382ca-fa4f-46d8-94f6-6f632b92cfb3", "聚碳酸酯板块", "聚碳酸酯（PC）行业。"),
    ("15298062-3e6f-4670-a4fc-f154fde599ed", "聚酯薄膜行业", "PET薄膜/聚酯薄膜行业。"),
    ("db958fdd-4159-4d1f-8aa3-e9806a3d2141", "脑机接口板块", "脑机接口（BCI）板块。"),
    ("278664fb-d81b-41dd-aad3-24f26cfda250", "腾讯云", "腾讯旗下云计算业务。"),
    ("28a89b0b-1273-4538-93e1-3bfce684e9fc", "船舶制造板块", "A股船舶制造板块。"),
    ("da57db3a-66bc-4ccf-ac97-768b54d4f917", "血制品板块", "血液制品行业（含天坛生物、华兰生物等）。"),
    ("89f41126-32b6-4502-a644-b85ed04bfb16", "血液制品板块", "A股血液制品行业板块（含天坛生物、华兰生物等）。"),
    ("4b8dbf85-28e1-429e-869f-e6f8259bb82c", "轨交设备板块", "A股轨交设备板块。"),
    ("0a2a80b6-67c8-4fec-98d8-cbc940e3790f", "酒店板块", "A股酒店行业板块。"),
    ("47715f62-e22b-430b-bd58-4a385fca0292", "钛白粉板块", "钛白粉行业板块。"),
    ("3dce08a0-9567-4585-bb64-dbcd474a3926", "钢铁板块", "A股钢铁行业板块。"),
    ("a648249b-dd19-4efd-925b-5b55b66db777", "钨板块", "A股钨概念板块。"),
    ("abc20c78-28ec-4f10-9d44-5e91f97c14da", "铁矿石板块", "A股铁矿石板块。"),
    ("31f33836-f7c4-42ac-8e6b-2a97b02c3ee1", "银发经济", "银发经济板块（养老服务/康复器具等）。"),
    ("a4c31b6a-0230-4578-8a60-874a7761b93a", "银行板块", "A股银行行业板块。"),
    ("8c709f8a-22fe-4341-83b4-cff350d0ee9c", "锂矿板块", "A股锂矿/锂电上游资源板块。"),
    ("2c11bdd0-0778-4ecc-835f-83dba4dd68d0", "韩国电池行业", "韩国三大电池制造商（LG/SDI/SK On）。"),
    ("fe23cdcb-6491-4839-b0ce-317041eec191", "风电板块", "A股风电行业板块（含整机/零部件）。"),
    ("1204e515-dea2-4a33-bb83-0e36c660f461", "黄酒板块", "A股黄酒行业板块。"),
]

# Suffixes to strip
suffixes = ["板块", "概念", "行业", "产业链", "产业"]

def normalize(name):
    n = name
    for s in suffixes:
        if n.endswith(s) and len(n) > len(s) + 1:
            n = n[:-len(s)]
            break
    return n

def levenshtein_ratio(s1, s2):
    if not s1 or not s2:
        return 0.0
    len1, len2 = len(s1), len(s2)
    d = [[0]*(len2+1) for _ in range(len1+1)]
    for i in range(len1+1):
        d[i][0] = i
    for j in range(len2+1):
        d[0][j] = j
    for i in range(1, len1+1):
        for j in range(1, len2+1):
            cost = 0 if s1[i-1] == s2[j-1] else 1
            d[i][j] = min(d[i-1][j]+1, d[i][j-1]+1, d[i-1][j-1]+cost)
    return 1.0 - d[len1][len2] / max(len1, len2)

def contains_check(n1, n2):
    """Returns True if n1 contains n2 or vice versa (after normalization)"""
    norm1 = normalize(n1)
    norm2 = normalize(n2)
    if len(norm1) < 2 or len(norm2) < 2:
        return False
    return norm1 in norm2 or norm2 in norm1

# Find duplicate pairs
print("=" * 80)
print("SECTOR DUPLICATE ANALYSIS")
print("=" * 80)

# Collect all duplicate pairs
duplicate_pairs = []

# Type 1: Name containment (after normalization)
for i in range(len(sectors)):
    for j in range(i+1, len(sectors)):
        id1, name1, desc1 = sectors[i]
        id2, name2, desc2 = sectors[j]
        if contains_check(name1, name2):
            duplicate_pairs.append(("CONTAINMENT", sectors[i], sectors[j]))

# Type 2: Same name after suffix removal
for i in range(len(sectors)):
    for j in range(i+1, len(sectors)):
        id1, name1, desc1 = sectors[i]
        id2, name2, desc2 = sectors[j]
        norm1, norm2 = normalize(name1), normalize(name2)
        if norm1 == norm2 and name1 != name2:
            duplicate_pairs.append(("EXACT_CORE", sectors[i], sectors[j]))

# Type 3: High string similarity
for i in range(len(sectors)):
    for j in range(i+1, len(sectors)):
        id1, name1, desc1 = sectors[i]
        id2, name2, desc2 = sectors[j]
        norm1, norm2 = normalize(name1), normalize(name2)
        sim = levenshtein_ratio(norm1, norm2)
        if sim >= 0.85 and norm1 != norm2 and not contains_check(name1, name2):
            duplicate_pairs.append((f"SIM={sim:.2f}", sectors[i], sectors[j]))

# Deduplicate the pairs list
seen_pairs = set()
unique_pairs = []
for pair_type, s1, s2 in duplicate_pairs:
    key = tuple(sorted([s1[0], s2[0]]))
    if key not in seen_pairs:
        seen_pairs.add(key)
        unique_pairs.append((pair_type, s1, s2))

print(f"\nTotal duplicate pairs found: {len(unique_pairs)}\n")

# Group into connected components
from collections import defaultdict
graph = defaultdict(set)
for _, s1, s2 in unique_pairs:
    graph[s1[0]].add(s2[0])
    graph[s2[0]].add(s1[0])

visited = set()
groups = []
for node_id in graph:
    if node_id not in visited:
        stack = [node_id]
        component = set()
        while stack:
            n = stack.pop()
            if n not in visited:
                visited.add(n)
                component.add(n)
                for neighbor in graph[n]:
                    if neighbor not in visited:
                        stack.append(neighbor)
        groups.append(component)

# Build id->sector lookup
id_to_sector = {s[0]: s for s in sectors}

for g_idx, group in enumerate(groups):
    group_sectors = [id_to_sector[sid] for sid in group]
    sorted_group = sorted(group_sectors, key=lambda x: len(x[2]))  # sort by desc length

    names = [s[1] for s in sorted_group]
    print(f"--- Duplicate Group {g_idx+1}: {' / '.join(names)} ---")

    # Classify type
    norm_names = [normalize(n) for n in names]
    if any(contains_check(names[a], names[b]) for a in range(len(names)) for b in range(a+1, len(names))):
        print("  Reason: NAME CONTAINMENT")
    elif all(n == norm_names[0] for n in norm_names):
        print("  Reason: SAME CORE NAME (different suffix)")
    else:
        print("  Reason: HIGH STRING SIMILARITY")

    print()
    for id, name, desc in sorted_group:
        print(f"  [{name}] id={id}")
        print(f"    normalized: \"{normalize(name)}\"")
        print(f"    description: {desc[:80]}...")

    # Find keep (most complete description) and merge targets
    best = sorted_group[-1]  # longest description
    print()
    print(f"  KEEP: \"{best[1]}\" (id={best[0]})")
    print(f"       desc: {best[2]}")
    for r in sorted_group[:-1]:
        print(f"  MERGE: \"{r[1]}\" (id={r[0]}) -> into \"{best[1]}\"")
    print()

# Also list semantically related pairs that are NOT caught by containment
print()
print("=" * 80)
print("SEMANTICALLY RELATED (subset/superset, not caught by containment)")
print("=" * 80)
print()

# Manual semantic checks
semantic_pairs = [
    ("人工智能板块", "AI大模型板块", "人工智能 is parent, AI大模型 is child (large overlap)"),
    ("人工智能板块", "AI算力板块", "人工智能 is parent, AI算力 is child"),
    ("人工智能板块", "A股AI应用板块", "人工智能 is parent, AI应用 is child"),
    ("具身智能板块", "人形机器人板块", "具身智能 is parent, 人形机器人 is child"),
    ("空天信息板块", "商业航天", "空天信息 is parent, 商业航天 is child (空天信息 desc mentions 商业航天)"),
    ("风电板块", "海上风电板块", "风电 is parent, 海上风电 is child"),
    ("汽车行业", "新能源汽车板块", "汽车 is parent, 新能源汽车 is child"),
    ("汽车行业", "汽车零部件板块", "汽车 is parent, 汽车零部件 is child"),
    ("光通信板块", "光模块板块", "光通信 is parent, 光模块 is child (光通信 desc mentions 光模块)"),
    ("电力板块", "火电板块", "电力 is parent, 火电 is child"),
    ("电力板块", "核电板块", "电力 is parent, 核电 is child"),
    ("电力板块", "风电板块", "电力 is parent, 风电 is child"),
    ("化工板块", "化纤板块", "化工 is parent, 化纤 is child"),
    ("化工板块", "化肥板块", "化工 is parent, 化肥 is child"),
    ("锂矿板块", "碳酸锂板块", "锂矿 and 碳酸锂 are upstream/downstream, same value chain"),
    ("钢铁板块", "铁矿石板块", "铁矿石 is upstream of 钢铁, related value chain"),
    ("有色金属板块", "电解铝板块", "有色金属 is parent, 电解铝 is child"),
    ("有色金属板块", "钨板块", "有色金属 is parent, 钨 is child"),
    ("纺织板块", "化纤板块", "化纤 supplies 纺织, related chain"),
    ("医疗器械板块", "检测检验板块", "医疗器械 includes 检测检验 in some contexts"),
    ("酒店板块", "在线旅游板块", "酒店 and 在线旅游 are related travel sectors"),
    ("煤炭板块", "火电板块", "煤炭 fuels 火电, related energy chain"),
    ("石油石化板块", "化纤板块", "石油石化 supplies 化纤 raw materials"),
    ("石油石化板块", "化肥板块", "石油石化 supplies 化肥 raw materials"),
    ("房地产板块", "水泥板块", "水泥 is upstream input to 房地产 construction"),
    ("变压器板块", "特高压板块", "变压器 is key equipment for 特高压"),
    ("变压器板块", "电力板块", "变压器 is component of 电力 infrastructure"),
    ("智能网联汽车板块", "新能源汽车板块", "智能网联汽车 and 新能源汽车 overlap on vehicle tech"),
]

id_to_sector = {s[0]: s for s in sectors}
name_to_id = {s[1]: s[0] for s in sectors}
name_to_desc = {s[1]: s[2] for s in sectors}

for n1, n2, reason in semantic_pairs:
    if n1 in name_to_id and n2 in name_to_id:
        id1 = name_to_id[n1]
        id2 = name_to_id[n2]
        # Check if already in a duplicate group
        already_grouped = False
        for group in groups:
            group_ids = {id_to_sector[sid][1] for sid in group}
            if n1 in group_ids or n2 in group_ids:
                already_grouped = True
                break
        if not already_grouped:
            print(f"  [{n1}] (id={id1})")
            print(f"  [{n2}] (id={id2})")
            print(f"  Relation: {reason}")
            print(f"  NOTE: These are distinct sectors but highly related (subset/superset).")
            print(f"        Consider whether they truly need separate nodes, or if hierarchy")
            print(f"        should express this relationship via edges instead of separate nodes.")
            print()
