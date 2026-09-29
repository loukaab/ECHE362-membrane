"""One source of equipment defaults in Mercury; N follows the notebook fit."""

from dataclasses import asdict, replace
from pathlib import Path

import pandas as pd
from scipy import stats

from .models import Equipment, MembraneProperties, validate_properties
from .numerics import PROJECT_ROOT
from .persistence import read_json, write_json


def fitted_new_membrane(path: Path | None = None) -> MembraneProperties:
    data = pd.read_csv(path or PROJECT_ROOT / "data" / "permeance_data_collection.csv")
    oxygen = data[data["Species"] == "O2"].melt(
        id_vars=["Species", "Feed Pressure (psig)"],
        value_vars=["Vp (scfh), Rep 1", "Vp (scfh), Rep 2"], value_name="Vp",
    )
    nitrogen = data[data["Species"] == "N2"]
    # Identical regression method, pilot area and standard-flow conversion to cell 4.
    oxygen_slope = stats.linregress(oxygen["Feed Pressure (psig)"], oxygen["Vp"]).slope
    nitrogen_slope = stats.linregress(nitrogen["Feed Pressure (psig)"], nitrogen["Vp (scfh), Rep 1"]).slope
    oxygen_permeance = float(oxygen_slope * 0.471947 / 9.86)
    nitrogen_permeance = float(nitrogen_slope * 0.471947 / 9.86)
    if nitrogen_permeance <= 0:
        raise ValueError("Experimental fit produced nonpositive N2 permeance")
    result = MembraneProperties(2500.0, oxygen_permeance / nitrogen_permeance, oxygen_permeance, "N")
    validate_properties(Equipment("preset", "N", "membrane", result))
    return result


class PresetStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else PROJECT_ROOT / "mercury" / "user_presets.json"
        self._builtins = {"O": MembraneProperties(4000.0, 5.5, 0.031, "O"), "N": fitted_new_membrane()}
        self._custom: dict[str, MembraneProperties] = {}
        self.warnings: list[str] = []
        self._load_failed = False
        if self.path.exists():
            try:
                doc = read_json(self.path)
                if doc.get("version") != 1 or not isinstance(doc.get("presets"), dict):
                    raise ValueError("Unsupported custom-preset document")
                loaded = {}
                for name, values in doc["presets"].items():
                    self._check_name(name)
                    p = replace(MembraneProperties(**values), preset=name)
                    validate_properties(Equipment("preset", name, "membrane", p))
                    loaded[name] = p
                self._custom = loaded
            except (ValueError, TypeError, OSError) as error:
                self._load_failed = True
                self.warnings.append(f"Custom presets could not be loaded from {self.path}: {error}. "
                                     "Built-in presets remain available; the file was not changed.")

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._builtins) + tuple(sorted(self._custom))

    def get(self, name: str) -> MembraneProperties:
        return replace(({**self._builtins, **self._custom})[name])

    def _check_name(self, name: str) -> None:
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            raise ValueError("Preset name must be nonempty and have no surrounding spaces")
        if name in self._builtins:
            raise ValueError("O and N are reserved built-in presets; choose another name")

    def save(self, name: str, properties: MembraneProperties) -> None:
        self._check_name(name)
        if self._load_failed:
            raise ValueError("Repair or move the invalid custom-preset file before saving presets")
        p = replace(properties, preset=name)
        validate_properties(Equipment("preset", name, "membrane", p))
        updated = {**self._custom, name: p}
        write_json(self.path, {"version": 1, "presets": {key: asdict(value) for key, value in updated.items()}})
        self._custom = updated
