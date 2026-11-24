import importlib
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.agents.base import BaseAgent

class AgentRegistry:
    def __init__(self):
        self._agents: dict[str, type["BaseAgent"]] = {}

    def register(self, name: str):
        def decorator(agent_class):
            previous = self._agents.get(name)
            if previous is not None and previous is not agent_class:
                raise ValueError(f"Agent already registered: {name}")
            self._agents[name] = agent_class
            return agent_class
        return decorator

    def load_modules(self, modules: list[str]) -> None:
        # Module names are trusted operator config; the API cannot supply modules.
        for module in modules:
            if not module.startswith("src.agents."):
                raise ValueError("Agent plugins must live in src.agents")
            importlib.import_module(module)

    def get(self, name: str):
        if name not in self._agents:
            raise ValueError(f"Unknown agent: {name}")
        return self._agents[name]

registry = AgentRegistry()
register_agent = registry.register
