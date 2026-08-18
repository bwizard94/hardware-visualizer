"""Parameter and modulation model.

One Param == one physical control on the finished panel. `base` is the knob
position (always normalised 0..1, exactly what a pot reads), and modulation is
summed in that same normalised space before being scaled to the parameter's
real range. Keeping knob-space and value-space separate is what lets a MIDI CC,
an audio band, and a physical pot all drive the same parameter without any of
them needing to know its units.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Param:
    key: str            # stable id, e.g. "glitch.amount" -- survives repatching
    label: str          # panel legend
    group: str          # effect it belongs to
    lo: float = 0.0     # real-world range, what the shader actually wants
    hi: float = 1.0
    base: float = 0.0   # knob position, 0..1

    # Modulation: one audio feature per parameter, with signed depth. The
    # hardware has no screen, so one source per control keeps it patchable
    # without menu diving.
    mod_source: str | None = None
    mod_depth: float = 0.0

    def resolve(self, features: dict[str, float]) -> float:
        """Knob position + modulation, clamped, then scaled to [lo, hi]."""
        n = self.base
        if self.mod_source:
            n += features.get(self.mod_source, 0.0) * self.mod_depth
        n = min(1.0, max(0.0, n))
        return self.lo + n * (self.hi - self.lo)

    def nudge(self, delta: float) -> None:
        self.base = min(1.0, max(0.0, self.base + delta))


class ParamBank:
    """Ordered registry of every parameter in the instrument.

    Order is meaningful: index N is intended to become physical pot N, so the
    declaration order in effects is also the panel layout order.
    """

    def __init__(self) -> None:
        self._params: dict[str, Param] = {}
        self.order: list[str] = []

    def add(self, param: Param) -> Param:
        if param.key in self._params:
            raise ValueError(f"duplicate param key: {param.key}")
        self._params[param.key] = param
        self.order.append(param.key)
        return param

    def get(self, key: str) -> Param:
        return self._params[key]

    def __contains__(self, key: str) -> bool:
        return key in self._params

    def __iter__(self):
        return (self._params[k] for k in self.order)

    def __len__(self) -> int:
        return len(self.order)

    def by_group(self, group: str) -> list[Param]:
        return [p for p in self if p.group == group]

    def resolve_all(self, features: dict[str, float]) -> dict[str, float]:
        return {p.key: p.resolve(features) for p in self}

    # --- presets -----------------------------------------------------------
    # Scene recall on the hardware is a button press; here it is a JSON file.

    def snapshot(self) -> dict:
        return {
            p.key: {
                "base": round(p.base, 4),
                "mod_source": p.mod_source,
                "mod_depth": round(p.mod_depth, 4),
            }
            for p in self
        }

    def restore(self, data: dict) -> None:
        for key, vals in data.items():
            if key not in self._params:
                continue  # preset from an older build, skip unknown controls
            p = self._params[key]
            p.base = float(vals.get("base", p.base))
            p.mod_source = vals.get("mod_source")
            p.mod_depth = float(vals.get("mod_depth", 0.0))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.snapshot(), indent=2))

    def load(self, path: Path) -> bool:
        if not path.exists():
            return False
        self.restore(json.loads(path.read_text()))
        return True
