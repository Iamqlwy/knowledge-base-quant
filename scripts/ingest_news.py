import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import traceback
from datetime import datetime, timezone
import pandas as pd

from kbquant.client import QuantClient, QuantClientError
from kbquant.schemas.information import RawInformationCreate
OFFSET_2026 = 0
OFFSET = 141000
SAMPLE_SIZE = 145

async def main():
    rows = []
    news_path = str(Path(__file__).parent.parent / "news.csv")
    rows = pd.read_csv(news_path).to_dict(orient="records")

    rows = rows[OFFSET+OFFSET_2026:OFFSET+OFFSET_2026+SAMPLE_SIZE]


    print(f"读取到 {len(rows)} 条 2026 年新闻，开始入库...")

    sem = asyncio.Semaphore(20)  # 控制并发数，避免打爆服务端

    async def ingest_one(i: int, row: dict):
        async with sem:
            content = row["content"].strip()
            published_at = datetime.strptime(row["datetime"], "%Y-%m-%d %H:%M:%S")

            if content.startswith("【") and "】" in content:
                title_end = content.index("】") + 1
                title = content[:title_end]
                body = content[title_end:].strip() or content
            else:
                title = content[:50]
                body = content

            data = RawInformationCreate(
                title=title,
                body=body,
                source="新浪财经",
                published_at=published_at,
                info_type="news",
                language="zh",
            )

            try:
                info = await client.information.ingest(data)
                print(f"[{i+1}/{len(rows)}] {info.id} | {title[:40]}")
            except QuantClientError as e:
                detail = f"status={e.status_code}" if hasattr(e, "status_code") else ""
                print(f"[{i+1}/{len(rows)}] 入库失败 [{type(e).__name__}]: {e.detail} {detail}")
                if hasattr(e, "error_code") and e.error_code:
                    print(f"        error_code={e.error_code}")
            except Exception as e:
                print(f"[{i+1}/{len(rows)}] 入库失败 [{type(e).__name__}]: {e}")
                traceback.print_exc()

    async with QuantClient("http://localhost:8000") as client:
        tasks = [ingest_one(i, row) for i, row in enumerate(rows)]
        await asyncio.gather(*tasks)

    print("完成。")


if __name__ == "__main__":
    asyncio.run(main())
