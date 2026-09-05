import asyncio

from openai import AsyncOpenAI

from kbquant.config import settings
from kbquant.integrations.embeddings.base import AbstractEmbeddingClient


class OpenAIEmbeddingClient(AbstractEmbeddingClient):
    def __init__(self):
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        )
        self._model = settings.embedding_model
        self._dimension = settings.embedding_dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        response = await self._client.embeddings.create(
            model=self._model,
            input=texts,
        )
        return [d.embedding for d in response.data]

    @property
    def dimension(self) -> int:
        return self._dimension
