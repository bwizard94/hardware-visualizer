"""Video sources feeding the mixer.

On the finished hardware the two inputs are digitised RGBS jacks. Here they are
whatever you point them at -- a camera, a video file, or a generated pattern.
Every source hands back a contiguous RGB uint8 array at canvas resolution, so
nothing downstream cares where the pixels came from.
"""

from __future__ import annotations

import threading
from pathlib import Path

import cv2
import numpy as np

from ..config import CANVAS_H, CANVAS_W


class Source:
    name = "source"

    def read(self) -> np.ndarray:
        raise NotImplementedError

    def close(self) -> None:
        pass


class TestPattern(Source):
    """Generated pattern. Always available, so the instrument still starts with
    nothing plugged in -- and the known-good bars are genuinely useful when
    calibrating analog levels later on.

    Two variants, so the two inputs can be told apart while testing the
    crossfade without needing two cameras.
    """

    def __init__(self, variant: str = "bars") -> None:
        self.variant = variant
        self.name = f"pattern:{variant}"
        self.frame_index = 0
        self._base = self._bars() if variant == "bars" else None
        if variant == "grid":
            self._wide = self._grid_buffer()

    @staticmethod
    def _bars() -> np.ndarray:
        bars = [
            (192, 192, 192), (192, 192, 0), (0, 192, 192), (0, 192, 0),
            (192, 0, 192), (192, 0, 0), (0, 0, 192), (19, 19, 19),
        ]
        base = np.zeros((CANVAS_H, CANVAS_W, 3), dtype=np.uint8)
        w = CANVAS_W // len(bars)
        for i, colour in enumerate(bars):
            x0 = i * w
            x1 = CANVAS_W if i == len(bars) - 1 else x0 + w
            base[: int(CANVAS_H * 0.75), x0:x1] = colour
        # Bottom quarter: a luma ramp, for checking gamma and clipping.
        ramp = np.linspace(0, 255, CANVAS_W, dtype=np.uint8)
        base[int(CANVAS_H * 0.75):] = ramp[None, :, None]
        return base

    @staticmethod
    def _grid_buffer() -> np.ndarray:
        """Double-wide checkerboard, built once.

        Composing this per frame instead costs ~7 ms of CPU -- enough to miss
        frame rate on its own with the GPU idle, and enough to hide real GPU
        regressions behind it in every benchmark. Built once, each frame is a
        contiguous slice copy instead. Motion comes from scrolling the window
        across it.
        """
        cell = CANVAS_H // 9
        ys = (np.arange(CANVAS_H) // cell) % 2
        xs = (np.arange(CANVAS_W * 2) // cell) % 2
        checker = (ys[:, None] ^ xs[None, :]).astype(np.uint8)
        palette = np.array([[24, 190, 220], [230, 60, 170]], dtype=np.uint8)
        return palette[checker]

    def _grid(self) -> np.ndarray:
        shift = int(self.frame_index * 2) % CANVAS_W
        return self._wide[:, shift:shift + CANVAS_W]

    def read(self) -> np.ndarray:
        if self.variant == "grid":
            frame = self._grid()
        else:
            frame = self._base.copy()
            x = int((self.frame_index * 6) % CANVAS_W)
            frame[:, max(0, x - 2): x + 2] = 255
        self.frame_index += 1
        return np.ascontiguousarray(frame)


class _ThreadedCapture(Source):
    """Shared machinery for cv2-backed sources.

    Capture runs on its own thread so a slow camera or a decode stall cannot
    drop the render loop below frame rate -- the chain just re-reads the last
    good frame.
    """

    loop = False

    def __init__(self) -> None:
        self.error: str | None = None
        self._frame = np.zeros((CANVAS_H, CANVAS_W, 3), dtype=np.uint8)
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._cap: cv2.VideoCapture | None = None

    def _open(self) -> cv2.VideoCapture | None:
        raise NotImplementedError

    def start(self) -> bool:
        cap = self._open()
        if cap is None:
            return False
        ok, _ = cap.read()
        if not ok:
            self.error = f"{self.name} opened but returned no frames"
            cap.release()
            return False
        self._cap = cap
        self._running = True
        self._thread = threading.Thread(target=self._loop_frames, daemon=True)
        self._thread.start()
        return True

    def _loop_frames(self) -> None:
        while self._running and self._cap is not None:
            ok, frame = self._cap.read()
            if not ok:
                if self.loop:
                    self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                break
            if frame.shape[0] != CANVAS_H or frame.shape[1] != CANVAS_W:
                frame = cv2.resize(frame, (CANVAS_W, CANVAS_H), interpolation=cv2.INTER_LINEAR)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            with self._lock:
                self._frame = np.ascontiguousarray(frame)

    def read(self) -> np.ndarray:
        with self._lock:
            return self._frame

    def close(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._cap is not None:
            self._cap.release()
            self._cap = None


class Webcam(_ThreadedCapture):
    def __init__(self, index: int = 0) -> None:
        super().__init__()
        self.index = index
        self.name = f"cam:{index}"

    def _open(self):
        cap = cv2.VideoCapture(self.index)
        if not cap.isOpened():
            self.error = f"could not open camera {self.index}"
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, CANVAS_W)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CANVAS_H)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # prefer latency over smoothness
        return cap


class VideoFile(_ThreadedCapture):
    """Looping file playback.

    Decode runs free of the render loop, so playback rate follows the file
    rather than the frame rate -- close enough for a source, and it keeps a
    long clip from ever stalling the instrument.
    """

    loop = True

    def __init__(self, path: str) -> None:
        super().__init__()
        self.path = Path(path).expanduser()
        self.name = f"file:{self.path.name}"

    def _open(self):
        if not self.path.exists():
            self.error = f"no such file: {self.path}"
            return None
        cap = cv2.VideoCapture(str(self.path))
        if not cap.isOpened():
            self.error = f"could not decode {self.path.name}"
            cap.release()
            return None
        return cap


def open_spec(spec: str) -> Source:
    """Open a source from a short spec. Never fails -- anything that cannot be
    opened falls back to a pattern, so the instrument always starts.

    Accepts: ``cam:N``, ``file:PATH``, ``bars``, ``grid``.
    """
    spec = spec.strip()
    if spec in ("bars", "grid"):
        return TestPattern(spec)

    source: Source | None = None
    if spec.startswith("cam:"):
        source = Webcam(int(spec.split(":", 1)[1]))
    elif spec.startswith("file:"):
        source = VideoFile(spec.split(":", 1)[1])
    else:
        print(f"  unrecognised source {spec!r}; using bars")
        return TestPattern("bars")

    if source.start():
        return source
    print(f"  {spec} unavailable ({source.error}); falling back to a pattern")
    return TestPattern("bars" if spec.startswith("cam") else "grid")
