import json
import os
from pathlib import Path

import ahocorasick

from kbquant.pipeline.scoring import EntityScorer, ScoringConfig

_project_root = Path(__file__).parent.parent.parent
DATA_DIR = os.path.join(_project_root, "data", "entities")

# 不做 IDF 降权的实体类型 — 这些类型的实体出现即有意义，不会被语料频率稀释
NON_IDF_TYPES = {"person", "event", "policy", "company", "index", "commodity",
                 "indicator", "central_bank", "regulation", "institution",
                 "product", "industry_rule", "concept", "geopolitical_event",
                 "natural_disaster", "epidemic", "key_technology",
                 "research_institution"}


def _load_entities() -> list[dict]:
    entities = []
    if not os.path.isdir(DATA_DIR):
        return entities
    for filename in sorted(os.listdir(DATA_DIR)):
        if not filename.endswith(".json"):
            continue
        with open(os.path.join(DATA_DIR, filename), "r", encoding="utf-8") as f:
            data = json.load(f)
        entities.extend(data)
    return entities


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x4E00 <= cp <= 0x9FFF
        or 0x3400 <= cp <= 0x4DBF
        or 0xF900 <= cp <= 0xFAFF
    )


# CJK 边界字符：这些单字在左侧邻接时通常表示合法的词边界
# 而非一个复合词的组成部分。如 "兑美元" 中 "兑" 是边界，"发电机" 中 "发" 不是。
_CJK_BOUNDARY_CHARS: set[str] = {
    # 介词/方向
    "兑", "对", "与", "和", "及", "或", "在", "从", "向", "将", "以",
    "为", "被", "把", "由", "于", "至", "经", "给", "让", "受",
    # 比较/数量
    "较", "比", "超", "约", "近", "逾", "满",
    # 动词（常与宾语搭配）
    "报", "跌", "涨", "升", "降", "买", "卖", "收", "付", "发",
    # 连接/修饰
    "的", "且", "并", "而", "但", "是", "有", "不", "未", "已",
    "等", "其", "该", "此", "之", "亦", "均", "仅",
    # 标点/符号的 CJK 等价
    "：", "。", "，", "、", "；", "！", "？", "）", "】", "」",
    "（", "【", "「",
}


def _is_boundary_char(ch: str) -> bool:
    """判断 CJK 字符是否为合法的词边界字符。"""
    return ch in _CJK_BOUNDARY_CHARS


def _is_word_boundary(text: str, pos: int, term: str) -> bool:
    """对短词做边界检查，防止"电机"误匹配"发电机"。

    左侧 CJK 检查：对 1-2 字的 CJK 词，检查左侧是否也是 CJK 字符。
    但若左侧字符是「边界字符」（如 兑、和、与、或、的、在、对、报 等），
    则认为是合法词边界，不阻断匹配。这解决了 "兑美元" 应匹配 "美元"
    而 "发电机" 不应匹配 "电机" 的歧义问题。
    右侧检查：仅对短 ASCII 词，CJK 右侧邻接不阻断。
    """
    cjk_chars = sum(1 for ch in term if _is_cjk(ch))
    alpha_chars = sum(1 for ch in term if ch.isascii() and ch.isalpha())

    # 左侧 CJK 检查：仅对 1-2 字短词生效
    need_left_cjk_check = cjk_chars > 0 and cjk_chars <= 2 and _is_cjk(term[0])
    # 短 ASCII 词检查（1-3 个字母）
    need_alpha_check = alpha_chars > 0 and alpha_chars <= 3

    if pos > 0:
        left = text[pos - 1]
        if need_left_cjk_check and _is_cjk(left) and not _is_boundary_char(left):
            return False
        if need_alpha_check and term[0].isalpha() and term[0].isascii() and left.isalnum() and left.isascii():
            return False

    # 右侧检查 —— 仅 ASCII-ASCII，不做 CJK-CJK
    end = pos + len(term)
    if end < len(text):
        right = text[end]
        if need_alpha_check and term[-1].isalpha() and term[-1].isascii() and right.isalnum() and right.isascii():
            return False

    return True


def _is_currency_unit(text: str, pos: int, term: str, entity_type: str) -> bool:
    """货币作为计价单位使用时不应识别为实体。

    检查两个方向：
    1. 数字在货币前："8000万人民币"、"8000万美元"
    2. 数字在货币后："人民币7.39亿元"、"美元100万"
    """
    if entity_type != "currency":
        return False

    # 检查前面：数字/数量词 + 货币
    i = pos - 1
    while i >= 0 and text[i] in "0123456789万千百十余多亿. ":
        if text[i] in "0123456789万千百十余多亿":
            return True
        i -= 1

    # 检查后面：货币 + 数字/数量（如 "人民币7.39亿元"）
    j = pos + len(term)
    while j < len(text) and text[j] in "0123456789万千百十余多亿. ":
        if text[j] in "0123456789":
            return True
        j += 1

    return False


def _has_exclusion_suffix(text: str, pos: int, term: str, entity: dict,
                          all_terms: set[str]) -> bool:
    """检查匹配位置后是否紧跟排除后缀，如"人民币"后跟"指数"→排除。"""
    suffixes = entity.get("metadata", {}).get("exclusion_suffixes", [])
    if not suffixes:
        return False

    suffix_start = pos + len(term)
    for suffix in suffixes:
        if text[suffix_start:suffix_start + len(suffix)] == suffix:
            # 如果 term+suffix 整体是一个已知实体，则不排除
            combined = term + suffix
            if combined in all_terms:
                return False
            return True
    return False


def _build_foreign_region_prefixes(entities: list[dict]) -> set[str]:
    """收集所有非中国 region 的 name + aliases，用于排除外国机构误匹配。"""
    prefixes: set[str] = set()
    for e in entities:
        if e.get("entity_type") != "region":
            continue
        if e["name"] in ("中国", "香港", "台湾", "中国大陆"):
            continue
        prefixes.add(e["name"])
        for a in e.get("aliases", []):
            if a not in ("CN",):
                prefixes.add(a)
    return prefixes


def _has_foreign_prefix(text: str, pos: int, foreign_regions: set[str]) -> bool:
    """检查匹配位置左侧是否紧邻外国地名。"""
    for prefix in foreign_regions:
        plen = len(prefix)
        if pos >= plen and text[pos - plen:pos] == prefix:
            return True
    return False


def _filter_contained(matches: list[dict]) -> list[dict]:
    """过滤掉被其他实体完全包含的匹配。

    仅当两个匹配属于同一实体时才做包含过滤。
    不同实体的包含关系（如"美联储主席"包含"美联储"）两个实体都保留，
    因为它们在语义上是不同的实体。
    """
    if len(matches) <= 1:
        return matches
    keep = []
    for i, a in enumerate(matches):
        a_end = a["position"] + len(a["matched_term"])
        contained = False
        for j, b in enumerate(matches):
            if i == j:
                continue
            b_end = b["position"] + len(b["matched_term"])
            # 仅当属于同一实体时才做包含过滤
            if a["name"] != b["name"]:
                continue
            if (b["position"] <= a["position"] and a_end <= b_end
                    and (b["position"] < a["position"] or a_end < b_end)):
                contained = True
                break
        if not contained:
            keep.append(a)
    return keep


def _build_all_terms(entities: list[dict]) -> set[str]:
    """收集所有实体的 name + aliases，用于排除后缀检查时判断组合词是否为已知实体。"""
    terms: set[str] = set()
    for entity in entities:
        terms.add(entity["name"])
        for alias in entity.get("aliases", []):
            if alias:
                terms.add(alias)
    return terms


class EntityMatcher:
    def __init__(self, entities: list[dict] | None = None,
                 idf_cache: dict[str, float] | None = None):
        self._entities = entities if entities is not None else _load_entities()
        self._idf_cache = idf_cache or {}
        self._foreign_regions = _build_foreign_region_prefixes(self._entities)
        self._all_terms = _build_all_terms(self._entities)
        self._build_automaton()

    def _build_automaton(self):
        self._automaton = ahocorasick.Automaton()
        for entity in self._entities:
            terms = [entity["name"]]
            for alias in entity.get("aliases", []):
                if alias and alias not in terms:
                    terms.append(alias)
            for term in terms:
                if not term or not term.strip():
                    continue
                self._automaton.add_word(term, (term, entity))
        self._automaton.make_automaton()

    @property
    def entity_count(self) -> int:
        return len(self._entities)

    def match(self, text: str, max_entities: int = 10) -> list[dict]:
        """Aho-Corasick 自动机一次扫描完成全部实体匹配。"""
        raw: list[dict] = []
        seen_keys: set[tuple[str, int]] = set()

        for end_idx, (term, entity) in self._automaton.iter(text):
            pos = end_idx - len(term) + 1

            dedup_key = (entity["name"], pos)
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            if not _is_word_boundary(text, pos, term):
                continue
            if _is_currency_unit(text, pos, term, entity.get("entity_type", "")):
                continue
            if _has_foreign_prefix(text, pos, self._foreign_regions):
                continue
            if _has_exclusion_suffix(text, pos, term, entity, self._all_terms):
                continue

            raw.append({
                "name": entity["name"],
                "entity_type": entity.get("entity_type", ""),
                "matched_term": term,
                "position": pos,
            })

        if not raw:
            return []

        raw.sort(key=lambda m: m["position"])

        deduped: dict[tuple, dict] = {}
        for m in raw:
            key = (m["name"], m["position"])
            if key not in deduped or len(m["matched_term"]) > len(deduped[key]["matched_term"]):
                deduped[key] = m

        matches = sorted(deduped.values(), key=lambda m: m["position"])
        matches = _filter_contained(matches)

        groups: dict[str, dict] = {}
        for m in matches:
            name = m["name"]
            if name not in groups:
                groups[name] = {
                    "name": name,
                    "entity_type": m["entity_type"],
                    "occurrences": 0,
                    "first_position": m["position"],
                    "matched_terms": [],
                }
            groups[name]["occurrences"] += 1
            groups[name]["matched_terms"].append(m["matched_term"])

        sorted_entities = sorted(groups.values(),
                                 key=lambda e: (-e["occurrences"], e["first_position"]))

        result = []
        for e in sorted_entities[:max_entities]:
            result.append({
                "name": e["name"],
                "entity_type": e["entity_type"],
                "occurrences": e["occurrences"],
                "first_position": e["first_position"],
                "matched_terms": list(dict.fromkeys(e["matched_terms"])),
            })
        return result

    def match_with_scores(self, text: str, title: str = "",
                          max_entities: int = 5) -> list[dict]:
        """匹配实体并用算法评分排序。

        先做字符串匹配，再用 TF-IDF + 位置 + 类型先验评分，
        通过重要性断层截断后返回，最多保留 max_entities 个。
        """
        matched = self.match(text, max_entities=30)
        if not matched:
            return []

        scorer = EntityScorer(idf_cache=self._idf_cache,
                              config=ScoringConfig(max_entities=max_entities))
        return scorer.score(matched, text, title=title)
