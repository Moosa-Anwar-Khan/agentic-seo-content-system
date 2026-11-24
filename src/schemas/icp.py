import json
from pathlib import Path
import yaml
from pydantic import Field
from src.schemas.common import StrictModel

class ICP(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    name: str
    description: str
    minimum_rented_properties: int | None = Field(None, ge=1)
    goals: list[str]
    services: list[str]
    preferred_traits: list[str]
    seed_topic: str
    drafting_approved: bool = False
    approved_fact_sources: list[str] = Field(default_factory=list)

icp_1_buy_and_hold = ICP(
    id="icp_1_buy_and_hold", name="Private Immobilieninvestoren – Buy & Hold",
    description="Private Investoren mit mindestens zwei vermieteten Immobilien oder einer Immobilie mit klarer Absicht zum Portfolioaufbau.", minimum_rented_properties=2,
    goals=["Langfristiger Vermögensaufbau", "Planbare steuerliche Begleitung", "Portfolioentwicklung"],
    services=["Einkommensteuer", "Anlage V", "AfA", "Kaufpreisaufteilung", "Finanzierung", "Eigentumsstruktur"],
    preferred_traits=["Digitale Belege", "Rechtzeitige Mitwirkung", "Vorausschauende Planung"],
    seed_topic="Immobiliensteuerberater",
)

def load_icp(path: Path) -> ICP:
    # safe_load disables arbitrary YAML object construction.
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.suffix in {".yaml", ".yml"} else json.loads(path.read_text(encoding="utf-8"))
    return ICP.model_validate(data)
