import asyncio
import copy
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select

from kbquant.config import settings
from kbquant.database import LazyDB
from kbquant.models.preference import IndustryCognition, StructuredPreference
from kbquant.models.preference import MarketCognition
from kbquant.services.llm_service import llm_service

logger = logging.getLogger(__name__)

_COGNITION_REWRITE_SYSTEM = (
    "你是一位资深投资分析师。请将以下碎片化的行业观察笔记整合为一份连贯、"
    "结构清晰的行业认知摘要。保留所有关键数据点、趋势判断和投资逻辑，"
    "去除重复内容，按逻辑顺序组织。直接输出整合后的认知文本，不要添加额外说明。"
)

_MARKET_COGNITION_REWRITE_SYSTEM = (
    "你是一位资深投资分析师。请将以下碎片化的市场整体观察笔记整合为一份连贯、"
    "结构清晰的市场认知摘要。保留所有关键数据点、趋势判断和投资逻辑，"
    "去除重复内容，按逻辑顺序组织。直接输出整合后的认知文本，不要添加额外说明。"
)


class PreferenceService:
    def __init__(self, db: LazyDB):
        self.db = db

    # ------------------------------------------------------------------
    # 内部辅助 (scoped — 接受 session 参数)
    # ------------------------------------------------------------------

    @staticmethod
    async def _get_latest_structured_scoped(session, *, for_update: bool = False) -> StructuredPreference | None:
        stmt = select(StructuredPreference).order_by(StructuredPreference.created_at.desc()).limit(1)
        if for_update:
            stmt = stmt.with_for_update()
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def _get_or_create_cognition_scoped(session, sector: str, *, custom_time: datetime | None = None,
                                               for_update: bool = False) -> IndustryCognition:
        stmt = select(IndustryCognition).where(IndustryCognition.sector == sector)
        if for_update:
            stmt = stmt.with_for_update()
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            row = IndustryCognition(sector=sector)
            if custom_time is not None:
                row.created_at = custom_time
                row.updated_at = custom_time
            session.add(row)
            await session.flush()
        return row

    async def _llm_rewrite_cognition(self, sector: str, existing_text: str) -> str:
        try:
            rewritten = await llm_service.chat(
                _COGNITION_REWRITE_SYSTEM,
                f"行业：{sector}\n\n碎片化笔记：\n{existing_text}",
                temperature=0.3,
            )
            return rewritten.strip()
        except Exception:
            logger.exception("LLM rewrite failed for sector=%s, keeping raw text", sector)
            return existing_text

    def _schedule_rewrite(self, sector: str, custom_time: datetime | None):
        async def _rewrite_and_persist():
            try:
                async with self.db.session() as session:
                    row = await self._get_or_create_cognition_scoped(session, sector, custom_time=custom_time)
                    if row.append_count < settings.preference_rewrite_threshold:
                        return
                    text_to_rewrite = row.cognition_text
                    count_snapshot = row.append_count

                rewritten = await self._llm_rewrite_cognition(sector, text_to_rewrite)

                async with self.db.session() as session:
                    row = await self._get_or_create_cognition_scoped(session, sector, custom_time=custom_time)
                    if row.append_count != count_snapshot:
                        return
                    row.cognition_text = rewritten
                    row.append_count = 0
                    if custom_time is not None:
                        row.updated_at = custom_time
                    await session.commit()
            except Exception:
                logger.exception("Background rewrite+persist failed for sector=%s", sector)

        asyncio.create_task(_rewrite_and_persist())

    async def _llm_rewrite_market_cognition(self, existing_text: str) -> str:
        try:
            rewritten = await llm_service.chat(
                _MARKET_COGNITION_REWRITE_SYSTEM,
                f"碎片化笔记：\n{existing_text}",
                temperature=0.3,
            )
            return rewritten.strip()
        except Exception:
            logger.exception("LLM rewrite failed for market cognition, keeping raw text")
            return existing_text

    # ------------------------------------------------------------------
    # 行业认知 & 市场认知
    # ------------------------------------------------------------------

    async def get_all_sectors(self) -> list[str]:
        async with self.db.session() as session:
            result = await session.execute(
                select(IndustryCognition.sector).order_by(IndustryCognition.sector)
            )
            return list(result.scalars().all())

    async def get_industry_cognition(self, sector: str) -> IndustryCognition | None:
        async with self.db.session() as session:
            result = await session.execute(
                select(IndustryCognition).where(IndustryCognition.sector == sector)
            )
            return result.scalar_one_or_none()

    async def get_market_cognition(self) -> MarketCognition | None:
        async with self.db.session() as session:
            result = await session.execute(
                select(MarketCognition).order_by(MarketCognition.created_at.desc()).limit(1)
            )
            return result.scalar_one_or_none()

    async def append_industry_cognition(self, sector: str, text: str,
                                        custom_time: datetime | None = None) -> tuple[str, str]:
        async with self.db.session() as session:
            row = await self._get_or_create_cognition_scoped(session, sector, custom_time=custom_time, for_update=True)

            if row.cognition_text:
                row.cognition_text = f"{row.cognition_text}\n\n---\n\n{text}"
            else:
                row.cognition_text = text
            row.append_count += 1
            if custom_time is not None:
                row.updated_at = custom_time
            await session.flush()

            threshold = settings.preference_rewrite_threshold
            if row.append_count >= threshold:
                self._schedule_rewrite(sector, custom_time)
                return sector, "appended"

            return sector, "appended"

    async def append_market_cognition(self, text: str,
                                      custom_time: datetime | None = None) -> str:
        async with self.db.session() as session:
            result = await session.execute(
                select(MarketCognition).order_by(MarketCognition.created_at.desc()).limit(1).with_for_update()
            )
            latest = result.scalar_one_or_none()
            current_text = latest.cognition_text if latest else ""

            if current_text:
                combined = f"{current_text}\n\n---\n\n{text}"
            else:
                combined = text

            new_row = MarketCognition(
                cognition_text=combined,
                append_count=0,
            )
            if custom_time is not None:
                new_row.created_at = custom_time
                new_row.updated_at = custom_time
            session.add(new_row)
            await session.flush()

        self._schedule_market_rewrite(combined, custom_time)
        return "appended"

    def _schedule_market_rewrite(self, raw_text: str, custom_time: datetime | None):
        async def _rewrite_and_persist():
            try:
                rewritten = await self._llm_rewrite_market_cognition(raw_text)
                async with self.db.session() as session:
                    result = await session.execute(
                        select(MarketCognition).order_by(MarketCognition.created_at.desc()).limit(1).with_for_update()
                    )
                    latest = result.scalar_one_or_none()
                    if latest is None or latest.cognition_text != raw_text:
                        return
                    new_row = MarketCognition(
                        cognition_text=rewritten,
                        append_count=0,
                    )
                    if custom_time is not None:
                        new_row.created_at = custom_time
                        new_row.updated_at = custom_time
                    session.add(new_row)
                    await session.commit()
            except Exception:
                logger.exception("Background market cognition rewrite failed")

        asyncio.create_task(_rewrite_and_persist())

    # ------------------------------------------------------------------
    # 结构化偏好
    # ------------------------------------------------------------------

    @staticmethod
    def _default_structured_values() -> dict[str, Any]:
        """读取模型列默认值作为首次写入前的基准状态。"""
        defaults = StructuredPreference()
        return {
            "asset_preferences": copy.deepcopy(defaults.asset_preferences),
            "risk_preferences": copy.deepcopy(defaults.risk_preferences),
            "analysis_preferences": copy.deepcopy(defaults.analysis_preferences),
            "learned_rules": copy.deepcopy(defaults.learned_rules),
        }

    async def get_structured(self) -> dict:
        async with self.db.session() as session:
            row = await self._get_latest_structured_scoped(session)

            result = await session.execute(select(IndustryCognition))
            cognitions = result.scalars().all()

            mc_result = await session.execute(
                select(MarketCognition).order_by(MarketCognition.created_at.desc()).limit(1)
            )
            mc = mc_result.scalar_one_or_none()

            if row is None:
                defaults = self._default_structured_values()
                return {
                    "id": None,
                    **defaults,
                    "industry_cognition": {c.sector: c.cognition_text for c in cognitions},
                    "industry_append_count": {c.sector: c.append_count for c in cognitions},
                    "market_cognition": mc.cognition_text if mc else None,
                    "market_append_count": mc.append_count if mc else 0,
                    "created_at": None,
                    "updated_at": None,
                }

            return {
                "id": row.id,
                "asset_preferences": row.asset_preferences,
                "risk_preferences": row.risk_preferences,
                "analysis_preferences": row.analysis_preferences,
                "learned_rules": row.learned_rules,
                "industry_cognition": {c.sector: c.cognition_text for c in cognitions},
                "industry_append_count": {c.sector: c.append_count for c in cognitions},
                "market_cognition": mc.cognition_text if mc else None,
                "market_append_count": mc.append_count if mc else 0,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }

    async def update_structured(
        self,
        *,
        asset_preferences: dict | None = None,
        risk_preferences: dict | None = None,
        analysis_preferences: dict | None = None,
        learned_rules: list | None = None,
        custom_time: datetime | None = None,
    ) -> StructuredPreference:
        async with self.db.session() as session:
            latest = await self._get_latest_structured_scoped(session, for_update=True)

            if latest is None:
                base = self._default_structured_values()
            else:
                base = {
                    "asset_preferences": copy.deepcopy(latest.asset_preferences),
                    "risk_preferences": copy.deepcopy(latest.risk_preferences),
                    "analysis_preferences": copy.deepcopy(latest.analysis_preferences),
                    "learned_rules": copy.deepcopy(latest.learned_rules),
                }

            if asset_preferences is not None:
                base["asset_preferences"] = copy.deepcopy(asset_preferences)
            if risk_preferences is not None:
                base["risk_preferences"] = copy.deepcopy(risk_preferences)
            if analysis_preferences is not None:
                base["analysis_preferences"] = copy.deepcopy(analysis_preferences)
            if learned_rules is not None:
                base["learned_rules"] = copy.deepcopy(learned_rules)

            new_row = StructuredPreference(**base)
            if custom_time is not None:
                new_row.created_at = custom_time
                new_row.updated_at = custom_time
            session.add(new_row)
            await session.flush()
            return new_row

    # ------------------------------------------------------------------
    # LLM 建议
    # ------------------------------------------------------------------

    async def apply_suggestions(self, payload: dict, *,
                                custom_time: datetime | None = None) -> dict:
        async with self.db.session() as session:
            latest = await self._get_latest_structured_scoped(session, for_update=True)

            applied: dict[str, list] = {
                "weight_changes": [],
                "risk_param_changes": [],
                "focus_points": [],
                "learned_rules_added": [],
            }

            if latest is None:
                base = self._default_structured_values()
            else:
                base = {
                    "asset_preferences": copy.deepcopy(latest.asset_preferences),
                    "risk_preferences": copy.deepcopy(latest.risk_preferences),
                    "analysis_preferences": copy.deepcopy(latest.analysis_preferences),
                    "learned_rules": copy.deepcopy(latest.learned_rules),
                }

            asset = base["asset_preferences"]
            risk = base["risk_preferences"]
            analysis = base["analysis_preferences"]
            rules = base["learned_rules"]

            for change in payload.get("weight_changes", []):
                asset["sector_weights"][change["sector"]] = change["new_weight"]
                applied["weight_changes"].append(change["sector"])

            for change in payload.get("risk_param_changes", []):
                risk[change["param_name"]] = change["new_value"]
                applied["risk_param_changes"].append(change["param_name"])

            for fp in payload.get("focus_points", []):
                if fp["action"] == "add" and fp["point"] not in analysis["focus_points"]:
                    analysis["focus_points"].append(fp["point"])
                    applied["focus_points"].append(fp["point"])
                elif fp["action"] == "remove" and fp["point"] in analysis["focus_points"]:
                    analysis["focus_points"].remove(fp["point"])
                    applied["focus_points"].append(fp["point"])

            for rule in payload.get("learned_rules_to_add", []):
                if rule not in rules:
                    rules.append(rule)
                    applied["learned_rules_added"].append(rule)

            new_row = StructuredPreference(
                asset_preferences=asset,
                risk_preferences=risk,
                analysis_preferences=analysis,
                learned_rules=rules,
            )
            if custom_time is not None:
                new_row.created_at = custom_time
                new_row.updated_at = custom_time
            session.add(new_row)
            await session.flush()

            return {"status": "applied", "applied_changes": applied}
