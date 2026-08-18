"""Entry point: python -m vsynth"""

from __future__ import annotations

import argparse
import sys

from .app import App
from .audio.analyzer import AudioAnalyzer
from .midi.router import MidiRouter


def main() -> None:
    # Line buffering, so piping the output to a log file still shows state
    # changes as they happen rather than only at exit.
    sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(prog="vsynth", description="audio-reactive video synth")
    parser.add_argument("--no-camera", action="store_true", help="use the test pattern")
    parser.add_argument("--camera", type=int, default=0, help="camera index")
    parser.add_argument("--audio", default=None, help="input device name or index")
    parser.add_argument("--midi", default=None, help="MIDI input port, matched by substring")
    parser.add_argument("--list", action="store_true", help="list audio and MIDI devices, then exit")
    args = parser.parse_args()

    if args.list:
        import sounddevice as sd
        print("audio inputs:")
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                print(f"  [{i}] {d['name']} ({d['max_input_channels']} ch)")
        print("midi inputs:")
        for name in MidiRouter.available_ports() or ["  (none)"]:
            print(f"  {name}")
        print(f"modulation sources: {', '.join(AudioAnalyzer.source_names())}")
        return

    device = args.audio
    if device is not None and device.isdigit():
        device = int(device)

    App(
        camera=not args.no_camera,
        camera_index=args.camera,
        audio_device=device,
        midi_port=args.midi,
    ).run()


if __name__ == "__main__":
    main()
