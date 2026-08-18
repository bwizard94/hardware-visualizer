"""Video sources feeding the effects chain.

On the finished hardware the primary sources are two digitised RGBS inputs;
here they are a webcam and a generated test pattern. Every source hands back a
contiguous RGB uint8 array at canvas resolution, so the chain never has to care
where the pixels came from.
"""

from __future__ import annotations

import threading

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
    """Colour bars with a moving sweep.

    Always available, so the instrument still starts with no camera attached --
    and the known-good bars are genuinely useful when calibrating analog levels
    later on.
    """

    name = "test pattern"

    def __init__(self) -> None:
        self.frame_index = 0
        bars = [
            (192, 192, 192), (192, 192, 0), (0, 192, 192), (0, 192, 0),
            (192, 0, 192), (192, 0, 0), (0, 0, 192), (19, 19, 19),
        ]
        self._base = np.zeros((CANVAS_H, CANVAS_W, 3), dtype=np.uint8)
        w = CANVAS_W // len(bars)
        for i, colour in enumerate(bars):
            x0 = i * w
            x1 = CANVAS_W if i == len(bars) - 1 else x0 + w
            self._base[: int(CANVAS_H * 0.75), x0:x1] = colour

        # Bottom quarter: a luma ramp, for checking gamma and clipping.
        ramp = np.linspace(0, 255, CANVAS_W, dtype=np.uint8)
        self._base[int(CANVAS_H * 0.75):] = ramp[None, :, None]

    def read(self) -> np.ndarray:
        frame = self._base.copy()
        x = int((self.frame_index * 6) % CANVAS_W)
        frame[:, max(0, x - 2): x + 2] = 255
        self.frame_index += 1
        return frame


class Webcam(Source):
    """Threaded camera capture.

    Capture runs on its own thread so a slow or stalling camera cannot drop the
    render loop below frame rate -- the chain just re-reads the last good frame.
    """

    name = "webcam"

    def __init__(self, index: int = 0) -> None:
        self.index = index
        self.error: str | None = None
        self._frame = np.zeros((CANVAS_H, CANVAS_W, 3), dtype=np.uint8)
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._cap: cv2.VideoCapture | None = None

    def start(self) -> bool:
        cap = cv2.VideoCapture(self.index)
        if not cap.isOpened():
            self.error = f"could not open camera {self.index}"
            cap.release()
            return False

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, CANVAS_W)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CANVAS_H)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # prefer latency over smoothness

        ok, _ = cap.read()
        if not ok:
            self.error = "camera opened but returned no frames (check permissions)"
            cap.release()
            return False

        self._cap = cap
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True

    def _loop(self) -> None:
        while self._running and self._cap is not None:
            ok, frame = self._cap.read()
            if not ok:
                continue
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


def open_source(prefer_camera: bool = True, index: int = 0) -> Source:
    """Camera if it is there, bars if it is not. Never fails."""
    if prefer_camera:
        cam = Webcam(index)
        if cam.start():
            return cam
        print(f"  camera unavailable ({cam.error}); falling back to test pattern")
    return TestPattern()
