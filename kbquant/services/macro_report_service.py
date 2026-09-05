import uuid
from datetime import datetime

from sqlalchemy import desc, select

from kbquant.database import LazyDB
from kbquant.models.macro_report import MacroReport


class MacroReportService:
    def __init__(self, db: LazyDB):
        self.db = db

    async def get_current(self) -> MacroReport | None:
        async with self.db.session() as session:
            result = await session.execute(
                select(MacroReport).order_by(desc(MacroReport.version)).limit(1)
            )
            report = result.scalar_one_or_none()
            if report is not None:
                session.expunge(report)
            return report

    async def update(self, *, content: str, summary: str,
                     changed_sections: list[str],
                     custom_time: datetime | None = None) -> MacroReport:
        async with self.db.session() as session:
            result = await session.execute(
                select(MacroReport).order_by(desc(MacroReport.version)).limit(1).with_for_update()
            )
            current = result.scalar_one_or_none()
            new_version = (current.version + 1) if current else 1

            report = MacroReport(
                version=new_version,
                content=content,
                summary=summary,
                changed_sections=changed_sections,
            )
            if custom_time is not None:
                report.created_at = custom_time
                report.updated_at = custom_time
            session.add(report)
            await session.flush()
            return report

    async def get_history(self, limit: int = 50) -> list[MacroReport]:
        async with self.db.session() as session:
            result = await session.execute(
                select(MacroReport).order_by(desc(MacroReport.version)).limit(limit)
            )
            reports = list(result.scalars().all())
            for r in reports:
                session.expunge(r)
            return reports
