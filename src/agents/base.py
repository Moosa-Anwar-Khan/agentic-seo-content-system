from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from src.config.settings import Settings
from src.storage.base import BaseStorage
from src.orchestrator.state import RunState
from src.security.sanitize import sanitize_payload, untrusted_context

@dataclass
class AgentContext:
    settings: Settings
    storage: BaseStorage
    llm: Any
    sources: Any

class BaseAgent(ABC):
    """Independent agents receive typed state and an explicit dependency context."""
    def __init__(self, context: AgentContext):
        self.context = context

    @abstractmethod
    def execute(self, state: RunState) -> RunState: ...

    def validate(self, state: RunState) -> None:
        RunState.model_validate(state.model_dump())

    def sanitize(self, payload):
        return sanitize_payload(payload)

    def human_feedback_prompt(self, state: RunState) -> str:
        # Notes stay in user data, under fixed system rules, with no tool authority.
        return untrusted_context({"revision_notes": state.human_feedback, "target": state.feedback_target_agent, "resolutions_required": True})
