"""Scene storage and recall.

A scene is everything that makes a look: every control position, every
modulation routing, and which effects are bypassed. It deliberately does *not*
include MIDI bindings, source selection or tempo -- those belong to the rig and
the gig, not to the look, and having a scene change stamp on them mid-set is
the kind of surprise a live instrument cannot afford.

Scenes persist the moment they are stored. There is no separate commit step,
because on a panel with no screen there would be nothing to show that a change
was still unsaved.
"""

from __future__ import annotations

import json
from pathlib import Path

# Six of the panel's twelve illuminated buttons. The other six are the five
# effect bypasses and tap tempo.
SLOTS = 6

# Note numbers for scene control over MIDI. Recall sits on C3 upward; store is
# an octave above, so storing is always a deliberate reach rather than a timing
# trick that could fire during a performance.
RECALL_NOTE_BASE = 60
STORE_NOTE_BASE = 72


class SceneBank:
    def __init__(self, bank, effects, path: Path, slots: int = SLOTS) -> None:
        self.bank = bank
        self.effects = effects
        self.path = path
        self.slots = slots
        self.scenes: dict[int, dict] = {}
        self.active: int | None = None
        # Set when a control is moved after a recall, so the LED for the active
        # scene can show "modified" rather than claiming the stored look.
        self.dirty = False

    # --- capture and apply -------------------------------------------------

    def _capture(self) -> dict:
        return {
            "params": self.bank.snapshot(),
            "bypass": {e.key: not e.enabled for e in self.effects},
        }

    def _apply(self, scene: dict) -> None:
        self.bank.restore(scene.get("params", {}))
        bypass = scene.get("bypass", {})
        for effect in self.effects:
            # Unknown effects default to enabled: a scene stored before an
            # effect existed should not silently bypass it.
            effect.enabled = not bypass.get(effect.key, False)

    # --- operations --------------------------------------------------------

    def store(self, slot: int) -> bool:
        if not 0 <= slot < self.slots:
            return False
        self.scenes[slot] = self._capture()
        self.active = slot
        self.dirty = False
        self.save()
        return True

    def recall(self, slot: int) -> bool:
        if slot not in self.scenes:
            return False
        self._apply(self.scenes[slot])
        self.active = slot
        self.dirty = False
        return True

    def revert(self) -> bool:
        """Reload the active scene, discarding edits since it was recalled."""
        return self.active is not None and self.recall(self.active)

    def clear(self, slot: int) -> bool:
        if slot not in self.scenes:
            return False
        del self.scenes[slot]
        if self.active == slot:
            self.active = None
        self.save()
        return True

    def filled(self) -> list[int]:
        return sorted(self.scenes)

    def touch(self) -> None:
        """Mark the active scene edited. Called when a control moves."""
        if self.active is not None:
            self.dirty = True

    # --- LED state ---------------------------------------------------------

    def led(self, slot: int) -> str:
        """What the button's lamp should show, for the hardware layer.

        off = empty, dim = stored, on = active, blink = active but edited.
        """
        if slot not in self.scenes:
            return "off"
        if slot != self.active:
            return "dim"
        return "blink" if self.dirty else "on"

    # --- persistence -------------------------------------------------------

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {str(slot): scene for slot, scene in sorted(self.scenes.items())}
        self.path.write_text(json.dumps(data, indent=2))

    def load(self) -> bool:
        if not self.path.exists():
            return False
        try:
            data = json.loads(self.path.read_text())
        except json.JSONDecodeError:
            print(f"  scenes file at {self.path} is unreadable; starting empty")
            return False
        self.scenes = {
            int(slot): scene for slot, scene in data.items()
            if slot.isdigit() and 0 <= int(slot) < self.slots
        }
        return bool(self.scenes)
