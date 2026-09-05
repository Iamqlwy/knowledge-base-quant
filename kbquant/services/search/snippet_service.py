"""Query-aware snippet 提取。

从候选文档中提取与查询最相关的文本片段，模仿浏览器搜索结果的 snippet:
- ~150 字符上限（中文每字约 2 字节视觉宽度，150 字对齐 Google 的 ~300px 宽度）
- 优先返回包含最多查询词的完整句子
- 没匹配时返回文档开头，不加 "[无关键词匹配]" 前缀
"""
import logging
import re

from kbquant.models.search_candidate import Candidate

logger = logging.getLogger(__name__)

_DISPLAY_MAX_LENGTH = 150
_SENTENCE_RE = re.compile(r'[^。！？\n]+[。！？\n]?')
_CJK_RE = re.compile(r'[一-鿿]')


class SnippetService:
    """从候选文档中提取与查询最相关的片段。"""

    def __init__(self, display_max_length: int = _DISPLAY_MAX_LENGTH):
        self.display_max_length = display_max_length

    def extract(
        self,
        query_text: str,
        candidates: list[Candidate],
    ) -> list[Candidate]:
        query_terms = self._tokenize_query(query_text)

        for c in candidates:
            full_text = self._get_full_text(c)
            if not full_text:
                continue
            c.snippet = self._extract_snippet_for_one(query_terms, full_text)

        return candidates

    def _tokenize_query(self, query_text: str) -> list[str]:
        """中文查询分词：标点切分 + 字符 bigram/trigram 组合。

        只保留全 CJK bigram/trigram，过滤跨词素的无意义组合。
        """
        text = query_text.strip()
        phrases = re.split(r'[\s，。、；：！？""''（）【】,.-]+', text)
        phrases = [p for p in phrases if len(p) >= 1]

        tokens = list(phrases)

        for phrase in phrases:
            if ' ' in phrase:
                continue
            chars = list(phrase)
            for i in range(len(chars) - 1):
                if _CJK_RE.match(chars[i]) and _CJK_RE.match(chars[i + 1]):
                    tokens.append(chars[i] + chars[i + 1])
            for i in range(len(chars) - 2):
                if all(_CJK_RE.match(c) for c in chars[i:i + 3]):
                    tokens.append(chars[i] + chars[i + 1] + chars[i + 2])

        seen = set()
        result = []
        for t in tokens:
            if t not in seen:
                seen.add(t)
                result.append(t)
        return result

    _TEXT_FIELDS = (
        "body", "content", "lessons_learned", "description",
        "state_summary", "core_logic", "node_type", "name",
    )

    @staticmethod
    def _get_full_text(candidate: Candidate) -> str:
        parts: list[str] = []
        if candidate.title:
            parts.append(candidate.title)

        if candidate.es_source:
            for field in SnippetService._TEXT_FIELDS:
                val = candidate.es_source.get(field, "")
                if val:
                    parts.append(val)
        elif candidate.raw:
            row = candidate.raw
            for field in SnippetService._TEXT_FIELDS:
                val = row.get(field) if isinstance(row, dict) else getattr(row, field, None)
                if val:
                    parts.append(str(val))

        return " ".join(parts)

    def _extract_snippet_for_one(
        self, query_terms: list[str], full_text: str,
    ) -> str:
        """从单篇文档中提取最相关的片段，模仿浏览器 snippet。

        1. 将文本按句子切分
        2. 对每个句子打分（查询词命中数 + 密度）
        3. 从最高分句子开始拼接，直到达到 display_max_length
        4. 没命中时返回文本开头
        """
        if not query_terms or not full_text:
            return full_text[:self.display_max_length]

        terms_lower = [t.lower() for t in query_terms]

        # 切句子
        sentences = _SENTENCE_RE.findall(full_text)
        if not sentences:
            return full_text[:self.display_max_length]

        # 对每个句子打分
        scored: list[tuple[float, int, str]] = []  # (score, orig_idx, sentence)
        for i, sent in enumerate(sentences):
            sent_lower = sent.lower()
            hits = sum(1 for t in terms_lower if t in sent_lower)
            if hits == 0:
                continue
            # 密度分数：命中数 / 句子长度，让精悍句子胜出
            density = hits / max(len(sent), 1)
            scored.append((hits + density * 0.5, i, sent.strip()))

        if not scored:
            return full_text[:self.display_max_length]

        # 按分数降序排列
        scored.sort(key=lambda x: x[0], reverse=True)

        # 从最高分句子开始拼接，控制总长度
        parts: list[str] = []
        total_len = 0
        for _, orig_i, sent in scored:
            if total_len + len(sent) <= self.display_max_length:
                parts.append(sent)
                total_len += len(sent)
            else:
                remaining = self.display_max_length - total_len
                if remaining > 20:
                    parts.append(sent[:remaining])
                break

        snippet = "。".join(parts)
        if not snippet:
            return full_text[:self.display_max_length]

        # 省略号标记（不额外加前缀）
        first_idx = scored[0][1]
        if first_idx > 0:
            snippet = "…" + snippet
        last_used_idx = max(s[1] for s in scored[:len(parts)])
        if last_used_idx < len(sentences) - 1:
            snippet = snippet + "…"

        return snippet[:self.display_max_length + 3]  # 留出省略号空间
