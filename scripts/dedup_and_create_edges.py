"""
合并重复 world_nodes，更新 node_states，创建 world_node_edges。

步骤：
1. 对 world_nodes 进行去重合并
2. 更新所有 node_states 的 updated_at 为 2026-03-31
3. 给节点之间创建合理的 world_node_edges（不强行关联无关节点）

用法: uv run python scripts/dedup_and_create_edges.py [--dry-run]
"""

import sys
import asyncio
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

sys.path.insert(0, str(Path(__file__).parent.parent))
from kbquant.config import settings

TARGET_DATE = datetime(2026, 3, 31, 0, 0, 0, tzinfo=timezone.utc)


def clean_name(name: str) -> str:
    for suf in ["板块", "概念", "行业", "赛道", "产业链", "领域", "产业"]:
        if name.endswith(suf):
            return name[:-len(suf)].strip()
    return name.strip()


def clean_desc(desc: str) -> str:
    return desc[:300].lower()


# =============================================================================
# Phase 1: 去重
# =============================================================================

def find_duplicates(nodes):
    merges_raw = []

    # 1a. 精确同名+同类型
    name_type_groups = defaultdict(list)
    for n in nodes:
        key = (n["name"].strip().lower(), n["node_type"])
        name_type_groups[key].append(n)
    for group in name_type_groups.values():
        if len(group) > 1:
            group.sort(key=lambda x: len(x["description"]), reverse=True)
            keeper = group[0]
            for dup in group[1:]:
                merges_raw.append((keeper["id"], dup["id"], "exact_name_type"))

    # 1b. sector vs concept 同名（去掉后缀）
    sectors = [n for n in nodes if n["node_type"] == "sector"]
    concepts = [n for n in nodes if n["node_type"] == "concept"]
    for c in concepts:
        cname_c = clean_name(c["name"]).lower()
        if len(cname_c) < 3:
            continue
        for s in sectors:
            sname_c = clean_name(s["name"]).lower()
            if len(sname_c) < 3:
                continue
            if cname_c == sname_c:
                merges_raw.append((s["id"], c["id"], "sc_exact"))
            else:
                sim = SequenceMatcher(None, cname_c, sname_c).ratio()
                if sim >= 0.88:
                    merges_raw.append((s["id"], c["id"], f"sc_sim_{sim:.2f}"))

    # 1c. 高相似度同类型 (>=0.92)
    for i, a in enumerate(nodes):
        aname_c = clean_name(a["name"]).lower()
        if len(aname_c) < 4:
            continue
        for j, b in enumerate(nodes):
            if i >= j:
                continue
            if a["node_type"] != b["node_type"]:
                continue
            bname_c = clean_name(b["name"]).lower()
            if len(bname_c) < 4:
                continue
            if aname_c == bname_c:
                continue
            sim = SequenceMatcher(None, aname_c, bname_c).ratio()
            if sim >= 0.92:
                keeper = a if len(a["description"]) >= len(b["description"]) else b
                to_merge = b if keeper is a else a
                merges_raw.append((keeper["id"], to_merge["id"], f"highsim_{sim:.2f}"))

    # 1d. 同 ticker company
    ticker_groups = defaultdict(list)
    for n in nodes:
        if n["ticker"] and n["node_type"] == "company":
            ticker_groups[n["ticker"]].append(n)
    for ticker, group in ticker_groups.items():
        if len(group) > 1:
            for i, a in enumerate(group):
                for j, b in enumerate(group):
                    if i >= j:
                        continue
                    sim = SequenceMatcher(None, a["name"].lower(), b["name"].lower()).ratio()
                    if sim >= 0.7:
                        keeper = a if len(a["description"]) >= len(b["description"]) else b
                        to_merge = b if keeper is a else a
                        merges_raw.append((keeper["id"], to_merge["id"], f"ticker_{ticker}"))
                        break

    # 去重
    merged_ids = set()
    final_merges = []
    for keep_id, merge_id, reason in merges_raw:
        if merge_id in merged_ids or keep_id == merge_id:
            continue
        if merge_id in {m[0] for m in final_merges}:
            continue
        merged_ids.add(merge_id)
        final_merges.append((keep_id, merge_id, reason))

    return final_merges


# =============================================================================
# Phase 2: 边
# =============================================================================

# 精确定义：描述中的关键词 -> (sector_name, priority)
# priority 越高越优先，用于去重（每个公司最多2个 sector）
COMPANY_SECTOR_RULES = [
    # AI/科技
    (["光模块", "光通信", "CPO", "1.6T", "800G"], "光通信板块", 10),
    (["AI芯片", "GPU芯片", "通用GPU", "AI推理芯片", "AI训练芯片"], "AI芯片概念", 10),
    (["人形机器人", "具身智能"], "机器人板块", 10),
    (["协作机器人"], "机器人板块", 9),
    (["碳化硅衬底", "碳化硅器件", "碳化硅功率"], "碳化硅板块", 10),
    (["存储芯片", "NAND", "NOR Flash", "DRAM", "HBM", "存储涨价"], "存储芯片板块", 10),
    (["半导体设备", "刻蚀设备", "薄膜沉积"], "半导体设备板块", 10),
    (["晶圆代工", "晶圆厂"], "半导体板块", 9),
    (["模拟芯片", "功率半导体", "IGBT", "MCU", "MOSFET"], "半导体板块", 9),
    (["碳纤维"], "碳纤维板块", 10),
    (["量子计算", "光量子"], "量子计算板块", 10),
    (["大模型", "AI大模型", "基座模型"], "AI大模型板块", 9),
    (["液冷", "冷却液", "浸没式"], "液冷板块", 9),
    (["PCB", "覆铜板", "高多层"], "PCB板块", 9),
    (["先进封装"], "先进封装板块", 10),
    (["RISC-V"], "半导体板块", 8),
    (["工业机器人"], "机器人板块", 8),
    (["机器视觉", "3D视觉"], "机器视觉板块", 9),
    (["激光雷达"], "激光雷达板块", 9),
    (["传感器"], "传感器板块", 8),

    # 新能源
    (["光伏", "硅片", "电池组件", "TOPCon"], "光伏行业", 10),
    (["储能", "储能系统", "储能电池", "储能电站"], "储能板块", 10),
    (["锂电池", "动力电池", "锂离子电池", "锂电设备"], "锂电池板块", 10),
    (["固态电池"], "固态电池", 10),
    (["正极材料"], "锂电池板块", 9),
    (["负极材料", "负极包覆"], "锂电池板块", 9),
    (["电池隔膜", "隔膜"], "锂电池板块", 9),
    (["电解液"], "锂电池板块", 9),
    (["氢燃料电池", "氢能"], "氢能源板块", 10),
    (["钠离子电池"], "钠离子电池", 10),
    (["风电"], "风电板块", 10),
    (["核电"], "核电板块", 10),

    # 资源/材料
    (["煤炭", "煤化工", "采煤"], "煤炭板块", 10),
    (["石油", "原油", "油气", "油田"], "石油化工板块", 10),
    (["钢铁", "钢材"], "钢铁板块", 10),
    (["铜矿", "铜冶炼", "铜加工"], "有色金属板块", 9),
    (["电解铝", "铝业", "铝加工", "水电铝"], "有色金属板块", 9),
    (["黄金", "金矿", "产金"], "黄金板块", 10),
    (["稀土"], "稀土板块", 10),
    (["钨"], "小金属板块", 10),
    (["锂矿", "锂盐", "锂业"], "锂矿板块", 10),
    (["钾肥"], "化肥板块", 10),
    (["尿素"], "化肥板块", 9),
    (["钛白粉"], "钛白粉板块", 10),
    (["有机硅", "工业硅"], "有机硅板块", 10),
    (["氟化工"], "氟化工板块", 10),
    (["水泥"], "水泥建材板块", 10),
    (["玻璃"], "玻璃板块", 10),
    (["石膏板"], "建材板块", 9),
    (["玻纤"], "建材板块", 8),
    (["天然橡胶"], "橡胶板块", 10),
    (["石墨电极", "石墨"], "石墨电极板块", 9),
    (["人造板"], "建材板块", 8),

    # 汽车/交通
    (["整车", "客车", "SUV", "商用车", "乘用车"], "汽车行业", 9),
    (["新能源汽车", "新能源车", "电动汽车", "电动车"], "新能源汽车", 10),
    (["智能驾驶", "自动驾驶", "ADAS", "Robotaxi"], "智能驾驶板块", 10),
    (["汽车零部件", "制动", "转向", "冲压零部件"], "汽车零部件板块", 9),
    (["飞行汽车", "eVTOL"], "飞行汽车概念", 10),

    # 消费/医药
    (["白酒"], "白酒板块", 10),
    (["啤酒"], "啤酒板块", 10),
    (["乳业", "乳制品", "液态奶", "原奶", "牛奶"], "乳业板块", 10),
    (["调味品", "酱油", "蚝油"], "调味品板块", 10),
    (["速冻食品", "火锅料", "水饺"], "食品饮料板块", 9),
    (["休闲食品", "鸡爪", "零食"], "食品饮料板块", 8),
    (["肉鸡", "白羽鸡", "肉制品加工", "猪养殖", "生猪"], "畜牧养殖板块", 10),
    (["能量饮料", "饮料"], "食品饮料板块", 8),
    (["茶饮", "新茶饮", "现制茶饮"], "餐饮板块", 9),
    (["餐饮", "火锅", "酸菜鱼", "中式餐饮", "连锁餐饮"], "餐饮板块", 10),
    (["旅游", "景区", "索道"], "旅游板块", 10),
    (["机票", "酒店", "旅行"], "OTA板块", 9),
    (["快递", "物流", "航运", "运输"], "快递物流板块", 9),
    (["电商", "电商代运营", "AI电商"], "电商板块", 10),
    (["黄金珠宝", "珠宝", "金饰"], "珠宝板块", 10),
    (["床垫", "家居", "家具"], "家居板块", 9),
    (["牙膏", "日化"], "日化板块", 9),
    (["烘焙", "蛋糕", "月饼"], "食品饮料板块", 8),
    (["体育用品", "运动品牌"], "体育用品板块", 10),
    (["汽车", "电动两轮车"], "电动自行车板块", 9),
    (["客车", "大巴"], "汽车行业", 9),

    # 医药 (放在后面确保不误匹配)
    (["创新药", "生物制药", "抗体", "PD-1", "PD-L1", "双抗", "ADC药物",
      "GLP-1", "小核酸", "乙肝治疗", "肿瘤", "新药", "临床"], "医药板块", 10),
    (["血液制品", "血制品", "人血白蛋白", "静注免疫球蛋白"], "血制品板块", 10),
    (["疫苗"], "疫苗板块", 10),
    (["中药", "中成药", "老字号"], "中药板块", 10),
    (["医疗器械", "影像设备", "X线探测器", "MRI"], "医疗器械板块", 10),
    (["体外诊断", "IVD"], "IVD板块", 10),
    (["医美", "肉毒素", "玻尿酸", "少女针"], "医美板块", 10),
    (["OK镜", "眼科"], "眼科板块", 9),
    (["原料药"], "医药板块", 8),
    (["核药", "RDC"], "医药板块", 9),

    # 金融
    (["银行", "商业银行", "发钞行", "不良贷款"], "银行板块", 10),
    (["券商", "证券公司", "投行", "证券经纪"], "券商板块", 10),
    (["保险", "寿险", "财险", "再保险", "保费"], "保险板块", 10),

    # 通信/TMT
    (["通信设备", "5G", "光通信", "光纤光缆", "光纤"], "通信设备板块", 10),
    (["SaaS", "OA协同", "ERP"], "SaaS板块", 9),
    (["游戏", "手游", "网游"], "游戏板块", 10),
    (["影视", "电影", "院线", "影院"], "影视板块", 10),
    (["数字营销", "效果广告", "GEO营销"], "广告营销板块", 9),
    (["数据中心", "IDC", "算力中心", "智算中心", "超算中心"], "IDC板块", 10),
    (["服务器", "服务器制造", "AI服务器"], "AI服务器产业链", 10),
    (["物联网", "无线通信模组"], "物联网板块", 9),
    (["视频生成", "视频压缩"], "AI视频板块", 9),
    (["网络安全", "信息安全", "网安"], "网络安全板块", 10),

    # 电力/能源
    (["电力", "电网", "发电", "风光发电", "新能源发电"], "电力板块", 9),
    (["特高压", "输变电"], "特高压板块", 10),
    (["变压器", "配电变压器"], "电力设备板块", 9),
    (["抽水蓄能", "抽蓄"], "电力板块", 9),
    (["智能电表"], "电力设备板块", 8),
    (["天然气", "燃气", "LNG"], "天然气板块", 10),
    (["油服", "油田服务", "钻井"], "油服板块", 10),
    (["供热"], "供热板块", 10),

    # 工程/建筑
    (["工程机械", "挖掘机", "盾构机"], "工程机械板块", 10),
    (["轨交", "高铁", "铁路", "轨道交通"], "轨交设备板块", 10),
    (["船舶制造", "集装箱船", "造船"], "船舶制造板块", 10),
    (["航空航天", "航天", "卫星"], "航天卫星板块", 10),
    (["军工", "国防", "特种装备", "制导"], "军工板块", 10),
    (["房地产", "地产", "房产"], "房地产板块", 9),
    (["建筑", "建筑施工", "工程总承包", "EPC", "水泥"], "建筑板块", 9),
    (["水务", "污水处理", "供水"], "水务板块", 10),
    (["环保", "垃圾焚烧", "固废"], "环保板块", 9),
    (["园林", "生态修复"], "园林板块", 9),
    (["防水材料", "防水"], "建材板块", 9),

    # 其他
    (["教育", "培训"], "教育板块", 10),
    (["纺织", "服装", "印染"], "纺织服装板块", 9),
    (["造纸", "瓦楞纸", "包装纸"], "造纸板块", 9),
    (["农业", "粮食", "种植", "种子", "饲料"], "农业板块", 10),
    (["信创", "国产替代"], "信创板块", 8),
    (["商业航天"], "商业航天", 10),
    (["低空经济"], "低空经济", 10),
    (["核能", "核燃料", "铀", "铀矿"], "铀矿板块", 10),
    (["工程咨询", "规划设计"], "建筑板块", 8),
    (["钛白粉", "钛"], "钛白粉板块", 10),
    (["草铵膦", "农药"], "农药板块", 10),
    (["纸包装"], "造纸板块", 8),
    (["集装箱", "集装箱制造"], "航运板块", 9),
    (["港口", "码头"], "港口板块", 9),
    (["客车"], "汽车行业", 9),
]

# macro_themes 到 sectors 的映射
SECTOR_TO_MACRO = {
    "AI大模型板块": "AI大模型",
    "AI芯片概念": "AI算力",
    "AI算力板块": "AI算力",
    "AI服务器产业链": "AI算力",
    "光通信板块": "AI算力",
    "先进封装板块": "AI算力",
    "存储芯片板块": "AI算力",
    "半导体板块": "国产替代",
    "半导体设备板块": "国产替代",
    "机器人板块": "机器人",
    "新能源汽车": "新能源转型",
    "光伏行业": "新能源转型",
    "风电板块": "新能源转型",
    "储能板块": "新能源转型",
    "锂电池板块": "新能源转型",
    "固态电池": "新能源转型",
    "氢能源板块": "新能源转型",
    "核电板块": "新能源转型",
    "量子计算板块": "量子计算",
    "飞行汽车概念": "低空经济",
    "低空经济": "低空经济",
    "商业航天": "低轨卫星与航天",
    "航天卫星板块": "低轨卫星与航天",
    "碳纤维板块": "新材料",
    "煤炭板块": "传统能源转型",
    "石油化工板块": "传统能源转型",
    "碳化硅板块": "新材料",
}


def _match_company_to_sector(company, sector_index):
    """根据描述关键词匹配 sector"""
    desc = clean_desc(company["description"])
    matches = []
    for keywords, sector_name, priority in COMPANY_SECTOR_RULES:
        for kw in keywords:
            if kw.lower() in desc:
                # 找到对应的 sector node
                for sector in sector_index.get(clean_name(sector_name).lower(), []):
                    matches.append((sector["id"], priority, kw))
                break

    # 按 priority 排序，取前2个
    matches.sort(key=lambda x: -x[1])
    seen_sid = set()
    result = []
    for sid, pri, kw in matches:
        if sid not in seen_sid:
            seen_sid.add(sid)
            result.append(sid)
        if len(result) >= 2:
            break
    return result


def generate_edges(nodes, node_by_id):
    edges = []
    seen = set()

    by_type = defaultdict(list)
    for n in nodes:
        by_type[n["node_type"]].append(n)

    # 构建索引
    sector_index = defaultdict(list)
    for s in by_type.get("sector", []):
        cname = clean_name(s["name"]).lower()
        sector_index[cname].append(s)
        sector_index[s["name"].strip().lower()].append(s)

    concept_index = {}
    for c in by_type.get("concept", []):
        cn = clean_name(c["name"]).lower()
        concept_index[cn] = c

    macro_index = {}
    for m in by_type.get("macro_theme", []):
        macro_index[clean_name(m["name"]).lower()] = m

    region_index = {}
    for r in by_type.get("region", []):
        region_index[r["name"].strip().lower()] = r

    def add_edge(parent_id, child_id, etype, weight=1.0):
        key = (parent_id, child_id, etype)
        if key not in seen and parent_id != child_id:
            seen.add(key)
            edges.append((parent_id, child_id, etype, round(weight, 2)))

    # ---- 2a. Company → Sector ----
    for company in by_type.get("company", []):
        matched_sids = _match_company_to_sector(company, sector_index)
        for sid in matched_sids:
            add_edge(sid, company["id"], "belongs_to", 0.8)

    # ---- 2b. Sector → Macro ----
    for sector in by_type.get("sector", []):
        sname_c = clean_name(sector["name"])
        if sname_c in SECTOR_TO_MACRO:
            macro_name = SECTOR_TO_MACRO[sname_c]
            macro = macro_index.get(clean_name(macro_name).lower())
            if macro:
                add_edge(macro["id"], sector["id"], "belongs_to", 0.9)

    # ---- 2c. Concept → Sector (概念归类到相关板块) ----
    for concept in by_type.get("concept", []):
        cdesc = clean_desc(concept["description"])
        cname_c = clean_name(concept["name"]).lower()
        for sector in by_type.get("sector", []):
            sname_c = clean_name(sector["name"]).lower()
            if len(sname_c) < 4:
                continue
            # 概念描述中明确提到该板块名
            if sname_c in cdesc:
                add_edge(sector["id"], concept["id"], "classified_as", 0.7)
                break
            # sector名比concept名称长且包含它 → 概念属于该板块子类
            if len(sname_c) > len(cname_c) and cname_c in sname_c:
                add_edge(sector["id"], concept["id"], "classified_as", 0.7)
                break

    # ---- 2d. Company → Region (based_in) ----
    region_aliases = {
        "深圳": ["深圳", "深圳市", "粤港澳大湾区", "深圳地区"],
        "贵州": ["贵州", "贵州省"],
        "青海": ["青海", "青海省"],
    }
    for company in by_type.get("company", []):
        desc = clean_desc(company["description"])
        name_lower = company["name"].lower()
        for rname, aliases in region_aliases.items():
            region = region_index.get(rname)
            if not region:
                continue
            for alias in aliases:
                if alias.lower() in desc or alias.lower() in name_lower:
                    add_edge(region["id"], company["id"], "based_in", 0.7)
                    break

    # ---- 2e. Policy → Sector ----
    for policy in by_type.get("policy", []):
        pdesc = clean_desc(policy["description"])
        for sector in by_type.get("sector", []):
            sname_c = clean_name(sector["name"]).lower()
            if len(sname_c) < 4:
                continue
            if sname_c in pdesc:
                add_edge(policy["id"], sector["id"], "regulated_by", 0.6)

    # ---- 2f. Sector → Sector nesting (名称包含) ----
    sectors = by_type.get("sector", [])
    for i, a in enumerate(sectors):
        aname_c = clean_name(a["name"]).lower()
        if len(aname_c) < 4:
            continue
        for j, b in enumerate(sectors):
            if i >= j:
                continue
            bname_c = clean_name(b["name"]).lower()
            if len(bname_c) < 4:
                continue
            if aname_c != bname_c:
                if len(aname_c) > len(bname_c) and bname_c in aname_c:
                    add_edge(a["id"], b["id"], "belongs_to", 0.6)
                elif len(bname_c) > len(aname_c) and aname_c in bname_c:
                    add_edge(b["id"], a["id"], "belongs_to", 0.6)

    # ---- 2g. Concept → Concept nesting ----
    concepts = by_type.get("concept", [])
    for i, a in enumerate(concepts):
        aname_c = clean_name(a["name"]).lower()
        if len(aname_c) < 4:
            continue
        for j, b in enumerate(concepts):
            if i >= j:
                continue
            bname_c = clean_name(b["name"]).lower()
            if len(bname_c) < 4:
                continue
            if aname_c != bname_c:
                if len(aname_c) > len(bname_c) and bname_c in aname_c:
                    add_edge(a["id"], b["id"], "belongs_to", 0.6)
                elif len(bname_c) > len(aname_c) and aname_c in bname_c:
                    add_edge(b["id"], a["id"], "belongs_to", 0.6)

    # ---- 2h. Person → Company/Institution ----
    for person in by_type.get("person", []):
        pname = person["name"].strip().lower()
        pdesc = clean_desc(person["description"])
        for company in by_type.get("company", []):
            cname = company["name"].strip().lower()
            if pname in clean_desc(company["description"]):
                add_edge(person["id"], company["id"], "led_by", 0.9)
        for inst in by_type.get("institution", []):
            iname = inst["name"].strip().lower()
            if iname in pdesc:
                add_edge(person["id"], inst["id"], "affiliated_with", 0.7)

    # ---- 2i. Product → Company ----
    for product in by_type.get("product", []):
        pname = product["name"].strip().lower()
        pdesc = clean_desc(product["description"])
        for company in by_type.get("company", []):
            cname = company["name"].strip().lower()
            if cname in pdesc or pname in clean_desc(company["description"]):
                add_edge(company["id"], product["id"], "has_business_segment", 0.7)

    # ---- 2j. Institution → Region ----
    for inst in by_type.get("institution", []):
        idesc = clean_desc(inst["description"])
        for rname, region in region_index.items():
            if rname in idesc:
                add_edge(region["id"], inst["id"], "based_in", 0.7)

    return edges


# =============================================================================
# Main
# =============================================================================

async def main(dry_run: bool = False):
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine)

    async with session_factory() as session:
        result = await session.execute(
            text("""SELECT id, name, node_type, description, ticker, aliases, is_active
                    FROM world_nodes WHERE is_active = true ORDER BY node_type, name""")
        )
        rows = result.fetchall()

    nodes = []
    for row in rows:
        nodes.append({
            "id": str(row[0]),
            "name": row[1],
            "node_type": row[2],
            "description": row[3] or "",
            "ticker": row[4],
            "aliases": list(row[5]) if row[5] else [],
            "is_active": row[6],
        })

    node_by_id = {n["id"]: n for n in nodes}

    by_type = defaultdict(list)
    for n in nodes:
        by_type[n["node_type"]].append(n)

    print(f"{'=' * 70}")
    print(f"节点总数: {len(nodes)}")
    print(f"各类型分布:")
    for nt, lst in sorted(by_type.items()):
        print(f"  {nt}: {len(lst)}")
    print(f"模式: {'DRY RUN (不改数据)' if dry_run else '实际执行'}")
    print(f"{'=' * 70}")

    # ==== Phase 1: 去重 ====
    print(f"\n{'=' * 70}")
    print("Phase 1: 重复节点检测与合并")
    print(f"{'=' * 70}")

    merges = find_duplicates(nodes)
    merge_map = {}

    if merges:
        print(f"发现 {len(merges)} 对重复节点:")
        for keep_id, merge_id, reason in merges:
            keeper = node_by_id.get(keep_id, {})
            to_merge = node_by_id.get(merge_id, {})
            print(f"  [{to_merge.get('node_type', '?')}] '{to_merge.get('name', '?')}' "
                  f"-> [{keeper.get('node_type', '?')}] '{keeper.get('name', '?')}' "
                  f"({reason})")
            merge_map[merge_id] = keep_id
    else:
        print("未发现需要合并的重复节点")

    if not dry_run and merges:
        async with session_factory() as session:
            for keep_id, merge_id, reason in merges:
                keeper = node_by_id.get(keep_id)
                to_merge = node_by_id.get(merge_id)
                if not keeper or not to_merge:
                    continue
                merged_aliases = list(set(
                    (keeper.get("aliases") or []) +
                    (to_merge.get("aliases") or []) +
                    [to_merge["name"]]
                ))
                merged_desc = keeper["description"]
                if len(to_merge["description"]) > len(keeper["description"]):
                    merged_desc = to_merge["description"]
                await session.execute(
                    text("""UPDATE world_nodes SET aliases = :a, description = :d,
                            updated_at = :ts WHERE id = :nid"""),
                    {"a": merged_aliases, "d": merged_desc,
                     "ts": TARGET_DATE, "nid": uuid.UUID(keep_id)})
                sres = await session.execute(
                    text("SELECT id FROM node_states WHERE node_id = :nid"),
                    {"nid": uuid.UUID(merge_id)})
                sids = [r[0] for r in sres.fetchall()]
                if sids:
                    # --- 处理版本冲突：迁移 node_states 前先重整版本号 ---
                    # 获取目标节点已有的版本号
                    existing_versions = await session.execute(
                        text("SELECT version FROM node_states WHERE node_id = :nid ORDER BY version"),
                        {"nid": uuid.UUID(keep_id)})
                    existing_vs = {r[0] for r in existing_versions.fetchall()}

                    # 获取源节点所有 states
                    src_states = await session.execute(
                        text("""SELECT id, version, effective_from, effective_to, core_logic,
                                        primary_drivers, risks, focus_points, recent_changes,
                                        uncertainty_flags, key_evidence_ids, state_summary,
                                        embedding, created_at
                                 FROM node_states WHERE node_id = :nid ORDER BY version"""),
                        {"nid": uuid.UUID(merge_id)})
                    src_rows = src_states.fetchall()

                    # 如果有版本冲突，删除目标节点中冲突版本的 state 或跳过
                    for srow in src_rows:
                        sv = srow[1]
                        if sv in existing_vs:
                            # 冲突：删除目标节点中同版本的旧state，保留源的
                            await session.execute(
                                text("DELETE FROM node_states WHERE node_id = :nid AND version = :v"),
                                {"nid": uuid.UUID(keep_id), "v": sv})
                            existing_vs.discard(sv)

                    # 迁移
                    await session.execute(
                        text("""UPDATE node_states SET node_id = :new_nid, updated_at = :ts
                                WHERE node_id = :old_nid"""),
                        {"new_nid": uuid.UUID(keep_id), "old_nid": uuid.UUID(merge_id),
                         "ts": TARGET_DATE})
                    print(f"  迁移 {len(sids)} 条 node_states: {merge_id[:8]} -> {keep_id[:8]}")
                await session.execute(
                    text("UPDATE world_nodes SET is_active = false, updated_at = :ts WHERE id = :nid"),
                    {"ts": TARGET_DATE, "nid": uuid.UUID(merge_id)})
            await session.commit()
            print(f"已合并 {len(merges)} 对重复节点")

    # ==== Phase 1b: 更新 updated_at ====
    print(f"\n{'=' * 70}")
    print("Phase 1b: 更新 node_states 和 world_nodes 的 updated_at 为 2026-03-31")
    print(f"{'=' * 70}")

    if not dry_run:
        async with session_factory() as session:
            await session.execute(
                text("UPDATE node_states SET updated_at = :ts"), {"ts": TARGET_DATE})
            await session.execute(
                text("UPDATE world_nodes SET updated_at = :ts WHERE is_active = true"),
                {"ts": TARGET_DATE})
            await session.commit()
            print("已更新所有 node_states 和 world_nodes 的 updated_at")

    # ==== Phase 2: 创建边 ====
    print(f"\n{'=' * 70}")
    print("Phase 2: 创建 world_node_edges")
    print(f"{'=' * 70}")

    async with session_factory() as session:
        result = await session.execute(
            text("""SELECT id, name, node_type, description, ticker, aliases
                    FROM world_nodes WHERE is_active = true ORDER BY node_type, name"""))
        rows = result.fetchall()

    active_nodes = []
    for row in rows:
        active_nodes.append({
            "id": str(row[0]),
            "name": row[1],
            "node_type": row[2],
            "description": row[3] or "",
            "ticker": row[4],
            "aliases": list(row[5]) if row[5] else [],
        })

    edges = generate_edges(active_nodes, {n["id"]: n for n in active_nodes})

    edge_type_counts = defaultdict(int)
    for _, _, etype, _ in edges:
        edge_type_counts[etype] += 1

    print(f"生成 {len(edges)} 条边:")
    for etype, count in sorted(edge_type_counts.items(), key=lambda x: -x[1]):
        print(f"  {etype}: {count}")

    # 按类型展示样例
    print(f"\n各类型样例:")
    shown_by_type = defaultdict(int)
    node_lookup = {n["id"]: n for n in active_nodes}
    edge_order = ["belongs_to", "classified_as", "based_in", "regulated_by",
                  "has_business_segment", "led_by", "affiliated_with"]
    for etype in edge_order:
        count = 0
        for parent_id, child_id, et, weight in edges:
            if et != etype:
                continue
            if count >= 5:
                break
            count += 1
            parent = node_lookup.get(parent_id, {})
            child = node_lookup.get(child_id, {})
            print(f"  [{parent.get('node_type', '?')}] '{parent.get('name', '?')}' "
                  f"--({et}, w={weight})--> "
                  f"[{child.get('node_type', '?')}] '{child.get('name', '?')}'")

    if not dry_run and edges:
        async with session_factory() as session:
            await session.execute(text("DELETE FROM world_node_edges"))
            await session.commit()
            edge_count = 0
            for parent_id, child_id, etype, weight in edges:
                try:
                    await session.execute(
                        text("""INSERT INTO world_node_edges
                                (parent_node_id, child_node_id, relationship_type,
                                 weight, created_at, updated_at)
                                VALUES (:pid, :cid, :rtype, :w, :ts, :ts)
                                ON CONFLICT (parent_node_id, child_node_id,
                                             relationship_type) DO NOTHING"""),
                        {"pid": uuid.UUID(parent_id), "cid": uuid.UUID(child_id),
                         "rtype": etype, "w": weight, "ts": TARGET_DATE})
                    edge_count += 1
                except Exception as e:
                    print(f"  跳过 ({parent_id[:8]}->{child_id[:8]}, {etype}): {e}")
            await session.commit()
            print(f"\n已创建 {edge_count} 条边")

    # ==== Summary ====
    print(f"\n{'=' * 70}")
    print("总结")
    print(f"{'=' * 70}")
    print(f"原始活跃节点数: {len(nodes)}")
    print(f"合并重复节点: {len(merges)} 对 → 合并后 {len(nodes) - len(merges)} 活跃节点")
    print(f"创建的边: {len(edges)} 条")
    print(f"updated_at 已更新为 2026-03-31")

    if dry_run:
        print(f"\n*** DRY RUN — 未修改数据库 ***")
        print(f"*** 确认无误后执行: uv run python scripts/dedup_and_create_edges.py ***")

    await engine.dispose()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    asyncio.run(main(dry_run=dry_run))
