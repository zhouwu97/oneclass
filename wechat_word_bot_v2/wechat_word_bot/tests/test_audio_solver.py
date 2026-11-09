from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

from bot.audio_solver import AudioSolver


class FakeDevice:
    def __init__(self, id: str, name: str, *, isloopback: bool = True):
        self.id = id
        self.name = name
        self.isloopback = isloopback


class AudioLoopbackSelectionTest(unittest.TestCase):
    def tearDown(self) -> None:
        sys.modules.pop("soundcard", None)

    def test_stale_explicit_device_name_falls_back_to_default_speaker_id(self) -> None:
        selected: list[str] = []
        loopback = FakeDevice(
            "{0.0.0.00000000}.{realtek-default}",
            "Speakers (Realtek(R) Audio)",
            isloopback=True,
        )
        default = FakeDevice(
            "{0.0.0.00000000}.{realtek-default}",
            "扬声器 (Realtek(R) Audio)",
            isloopback=False,
        )

        fake_soundcard = types.SimpleNamespace()

        def get_microphone(id: str, include_loopback: bool = False):
            selected.append(id)
            if id == loopback.id and include_loopback:
                return loopback
            raise IndexError(f"no device with id {id}")

        fake_soundcard.get_microphone = get_microphone
        fake_soundcard.default_speaker = lambda: default
        fake_soundcard.all_microphones = lambda include_loopback=False: [loopback]
        sys.modules["soundcard"] = fake_soundcard

        with tempfile.TemporaryDirectory() as tmp:
            solver = AudioSolver(
                {"audio_loopback_name": "扬声器 (Realtek(R) Audio)"},
                Path(tmp),
            )
            self.assertIs(solver._get_loopback(), loopback)

        self.assertEqual(
            selected,
            [
                "扬声器 (Realtek(R) Audio)",
                "{0.0.0.00000000}.{realtek-default}",
            ],
        )

    def test_device_list_includes_id_for_support_logs(self) -> None:
        loopback = FakeDevice("wasapi-id", "Speakers", isloopback=True)
        fake_soundcard = types.SimpleNamespace()
        fake_soundcard.all_microphones = lambda include_loopback=False: [loopback]
        sys.modules["soundcard"] = fake_soundcard

        with tempfile.TemporaryDirectory() as tmp:
            solver = AudioSolver({}, Path(tmp))
            self.assertEqual(
                solver.list_devices(),
                ["Speakers | id=wasapi-id | loopback=True"],
            )

    def test_uses_first_loopback_when_names_do_not_match(self) -> None:
        loopback = FakeDevice(
            "{0.0.0.00000000}.{different-device}",
            "Realtek HD Audio loopback",
            isloopback=True,
        )
        default = FakeDevice(
            "{0.0.0.00000000}.{default-speaker}",
            "扬声器 (Realtek(R) Audio)",
            isloopback=False,
        )

        fake_soundcard = types.SimpleNamespace()
        fake_soundcard.get_microphone = lambda id, include_loopback=False: (_ for _ in ()).throw(
            IndexError(f"no device with id {id}")
        )
        fake_soundcard.default_speaker = lambda: default
        fake_soundcard.all_microphones = lambda include_loopback=False: [loopback]
        sys.modules["soundcard"] = fake_soundcard

        with tempfile.TemporaryDirectory() as tmp:
            solver = AudioSolver({"audio_loopback_name": ""}, Path(tmp))
            self.assertIs(solver._get_loopback(), loopback)

    def test_loopback_device_is_cached_until_cleared(self) -> None:
        selected: list[str] = []
        loopback = FakeDevice("loopback-id", "Speakers", isloopback=True)
        fake_soundcard = types.SimpleNamespace()

        def get_microphone(id: str, include_loopback: bool = False):
            selected.append(id)
            if id == loopback.id and include_loopback:
                return loopback
            raise IndexError(f"no device with id {id}")

        fake_soundcard.get_microphone = get_microphone
        fake_soundcard.all_microphones = lambda include_loopback=False: [loopback]
        sys.modules["soundcard"] = fake_soundcard

        with tempfile.TemporaryDirectory() as tmp:
            solver = AudioSolver({"audio_loopback_name": loopback.id}, Path(tmp))
            self.assertIs(solver._get_loopback(), loopback)
            self.assertIs(solver._get_loopback(), loopback)
            solver._clear_loopback_cache()
            self.assertIs(solver._get_loopback(), loopback)

        self.assertEqual(selected, ["loopback-id", "loopback-id"])


if __name__ == "__main__":
    unittest.main()
