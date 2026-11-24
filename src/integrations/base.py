from abc import ABC, abstractmethod
from src.schemas.content import Keyword

class SearchDataSource(ABC):
    @abstractmethod
    def keywords(self, seed: str) -> list[Keyword]: ...

class PublicContentSource(ABC):
    @abstractmethod
    def pages(self) -> list[dict[str, str]]: ...

# These narrow methods can be exposed by an MCP server without changing agents.
