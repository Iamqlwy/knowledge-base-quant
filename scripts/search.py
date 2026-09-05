from kbquant.client import QuantClient
from kbquant.schemas.search import SearchRequest
import asyncio
query  ="""
攀钢 西昌钢钒 氢能 项目 钢铁 绿色转型
"""

async def main():
    response  = await QuantClient().search.search(SearchRequest(query_text=query))
    for r in response.items:
        print(r)

asyncio.run(main())
