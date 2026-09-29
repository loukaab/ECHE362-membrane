"""Simulation inputs and results; no graphical widget owns model state."""

from dataclasses import dataclass, field
from typing import TypeAlias
import math
import re

from .units import ATM_PSIA, finite


@dataclass(frozen=True)
class Stream:
    name: str
    flow_slpm: float
    pressure_psia: float
    composition: dict[str, float]

    def __post_init__(self) -> None:
        if finite(self.flow_slpm, "Flow (slpm)") < 0:
            raise ValueError("Flow must be nonnegative")
        if finite(self.pressure_psia, "Pressure (psia)") <= 0:
            raise ValueError("Absolute pressure must be positive")
        if set(self.composition) != {"O2", "N2"}:
            raise ValueError("This version requires O2 and N2 compositions")
        for species, value in self.composition.items():
            if not 0 <= finite(value, species + " mole fraction") <= 1:
                raise ValueError(f"{species} mole fraction must lie between 0 and 1")
        if not math.isclose(sum(self.composition.values()), 1.0, abs_tol=1e-10):
            raise ValueError("Mole fractions must sum to 1")
        object.__setattr__(self, "composition", dict(self.composition))

    @classmethod
    def binary(cls, name: str, flow_slpm: float, oxygen: float, pressure_psia: float) -> "Stream":
        return cls(name, flow_slpm, pressure_psia, {"O2": oxygen, "N2": 1 - oxygen})

    @property
    def oxygen(self) -> float:
        return self.composition["O2"]

    @property
    def temperature_c(self) -> float:
        """Display assumption only: this material model has no energy balance."""
        return 21.0


@dataclass(frozen=True)
class FeedProperties:
    flow_slpm: float = 17500.0
    oxygen: float = 0.209
    pressure_psia: float = ATM_PSIA


@dataclass(frozen=True)
class MembraneProperties:
    area_m2: float
    alpha: float
    oxygen_permeance: float
    preset: str = "Custom"
    permeate_pressure_psia: float = ATM_PSIA


@dataclass(frozen=True)
class CompressorProperties:
    rpm: int = 4500
    mode: str = "inlet"  # inlet, flow, or pressure
    flow_slpm: float | None = None
    outlet_pressure_psia: float | None = None


@dataclass(frozen=True)
class SplitterProperties:
    fraction: float = 0.5


@dataclass(frozen=True)
class ProductProperties:
    role: str = "report"  # oxygen, nitrogen, or report


@dataclass(frozen=True)
class EmptyProperties:
    pass


Properties: TypeAlias = (FeedProperties | MembraneProperties | CompressorProperties |
                         SplitterProperties | ProductProperties | EmptyProperties)


LEGEND_ID = "@legend"  # Reserved selection ID; the legend is not process equipment.


@dataclass(frozen=True)
class TextAnnotation:
    id: str
    text: str = "Text"
    x: float = 100.0
    y: float = 100.0
    width: float = 240.0
    font_size: int = 11
    bold: bool = False
    color: str = "#243b55"

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id.strip() or self.id == LEGEND_ID:
            raise ValueError("Textbox ID must be nonempty and must not use the reserved legend ID")
        if not isinstance(self.text, str):
            raise ValueError("Textbox content must be text")
        finite(self.x, "Textbox x")
        finite(self.y, "Textbox y")
        if finite(self.width, "Textbox width") <= 0:
            raise ValueError("Textbox width must be positive")
        if type(self.font_size) is not int or self.font_size <= 0:
            raise ValueError("Font size must be a positive integer")
        if type(self.bold) is not bool:
            raise ValueError("Textbox bold must be true or false")
        if not isinstance(self.color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", self.color):
            raise ValueError("Text color must be a six-digit hex color, such as #243b55")


@dataclass(frozen=True)
class LegendPosition:
    x: float = 40.0
    y: float = 20.0

    def __post_init__(self):
        finite(self.x, "Legend x")
        finite(self.y, "Legend y")

# Ordered ports determine both graph validation and Canvas port placement.
PORTS = {
    "feed": ((), ("outlet",)),
    "membrane": (("inlet",), ("retentate", "permeate")),
    "compressor": (("inlet",), ("outlet",)),
    "splitter": (("inlet",), ("a", "b")),
    "mixer": (("a", "b"), ("outlet",)),
    "product": (("inlet",), ()),
    "vent": (("inlet",), ()),
}
PROPERTY_TYPES = {
    "feed": FeedProperties, "membrane": MembraneProperties, "compressor": CompressorProperties,
    "splitter": SplitterProperties, "mixer": EmptyProperties, "product": ProductProperties,
    "vent": EmptyProperties,
}


@dataclass
class Equipment:
    id: str
    name: str
    kind: str
    properties: Properties
    x: float = 100.0
    y: float = 100.0
    width: float = 170.0
    height: float = 86.0
    label_dx: float = 0.0
    label_dy: float = -32.0

    @property
    def inlet_ports(self) -> tuple[str, ...]:
        return PORTS[self.kind][0]

    @property
    def outlet_ports(self) -> tuple[str, ...]:
        return PORTS[self.kind][1]


@dataclass(frozen=True)
class Connection:
    id: str
    name: str
    source_node: str
    source_port: str
    target_node: str | None = None
    target_port: str | None = None
    # A terminal stream has no target equipment; it owns its collection role.
    outlet: str | None = None  # oxygen, nitrogen, report (collected), or vent
    endpoint: tuple[float, float] | None = None
    waypoints: tuple[tuple[float, float], ...] = ()
    label_dx: float = 0.0
    label_dy: float = 0.0

    def __post_init__(self):
        def point(value):
            if not isinstance(value, (tuple, list)) or len(value) != 2:
                raise ValueError("Stream coordinates must contain x and y")
            return (finite(value[0], "Stream x"), finite(value[1], "Stream y"))
        if self.endpoint is not None:
            object.__setattr__(self, "endpoint", point(self.endpoint))
        if not isinstance(self.waypoints, (tuple, list)):
            raise ValueError("Stream waypoints must be a list of coordinate pairs")
        object.__setattr__(self, "waypoints", tuple(point(p) for p in self.waypoints))
        finite(self.label_dx, "Stream label x")
        finite(self.label_dy, "Stream label y")

    @property
    def is_terminal(self) -> bool:
        return self.target_node is None and self.target_port is None


@dataclass
class EquipmentResult:
    inlets: dict[str, Stream] = field(default_factory=dict)
    outlets: dict[str, Stream] = field(default_factory=dict)
    stage_cut: float | None = None
    ideal_power_kw: float | None = None


@dataclass(frozen=True)
class ProductResult:
    name: str
    role: str
    stream: Stream
    flow_met: bool | None
    composition_met: bool | None

    @property
    def passed(self) -> bool | None:
        return None if self.role == "report" else bool(self.flow_met and self.composition_met)


@dataclass
class SimulationResult:
    streams: dict[str, Stream] = field(default_factory=dict)
    equipment: dict[str, EquipmentResult] = field(default_factory=dict)
    products: dict[str, ProductResult] = field(default_factory=dict)
    vents: dict[str, Stream] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    execution_order: list[str] = field(default_factory=list)
    flow_balance_error_slpm: float = 0.0
    oxygen_balance_error_slpm: float = 0.0


def evaluate_product(name: str, role: str, stream: Stream) -> ProductResult:
    if role == "oxygen":
        return ProductResult(name, role, stream, stream.flow_slpm >= 3400, stream.oxygen >= 0.40)
    if role == "nitrogen":
        return ProductResult(name, role, stream, stream.flow_slpm >= 6000, stream.oxygen <= 0.05)
    if role != "report":
        raise ValueError("Unknown product role")
    return ProductResult(name, role, stream, None, None)


def validate_properties(equipment: Equipment) -> None:
    if not isinstance(equipment.id, str) or not equipment.id.strip():
        raise ValueError("Equipment ID must be a nonempty string")
    if not isinstance(equipment.name, str) or not equipment.name.strip():
        raise ValueError("Equipment name must be nonempty")
    if equipment.kind not in PROPERTY_TYPES:
        raise ValueError(f"Unknown equipment type {equipment.kind!r}")
    p = equipment.properties
    if not isinstance(p, PROPERTY_TYPES[equipment.kind]):
        raise ValueError("Equipment properties do not match its type")
    finite(equipment.x, "Position x")
    finite(equipment.y, "Position y")
    if finite(equipment.width, "Box width") < 100 or finite(equipment.height, "Box height") < 64:
        raise ValueError("Equipment boxes must be at least 100 × 64 diagram units")
    finite(equipment.label_dx, "Label x")
    finite(equipment.label_dy, "Label y")
    if isinstance(p, FeedProperties):
        Stream.binary(equipment.name, p.flow_slpm, p.oxygen, p.pressure_psia)
    elif isinstance(p, MembraneProperties):
        for label, value in (("Area (m²)", p.area_m2), ("Alpha", p.alpha),
                             ("O2 permeance", p.oxygen_permeance),
                             ("Permeate pressure (psia)", p.permeate_pressure_psia)):
            if finite(value, label) <= 0:
                raise ValueError(label + " must be positive")
        if not isinstance(p.preset, str):
            raise ValueError("Membrane preset name must be text")
    elif isinstance(p, CompressorProperties):
        if isinstance(p.rpm, bool) or not isinstance(p.rpm, int) or p.rpm <= 0:
            raise ValueError("Compressor RPM must be a positive integer")
        if p.mode not in ("inlet", "flow", "pressure"):
            raise ValueError("Compressor mode must be inlet, flow, or pressure")
        if p.mode == "flow" and finite(p.flow_slpm, "Compressor flow") < 0:
            raise ValueError("Compressor flow must be nonnegative")
        if p.mode == "pressure" and finite(p.outlet_pressure_psia, "Outlet pressure") <= 0:
            raise ValueError("Compressor outlet pressure must be positive")
    elif isinstance(p, SplitterProperties):
        if not 0 < finite(p.fraction, "Split fraction") < 1:
            raise ValueError("Split fraction must be strictly between 0 and 1")
    elif isinstance(p, ProductProperties) and p.role not in ("oxygen", "nitrogen", "report"):
        raise ValueError("Product role must be oxygen, nitrogen, or report")
