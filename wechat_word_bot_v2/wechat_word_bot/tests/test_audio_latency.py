from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

import numpy as np

from bot.audio_solver import AudioSolver


class AudioLatencyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.solver = AudioSolver({"audio_min_record_seconds": 0}, Path(self.tmp.name))

    def test_recording_stops_after_speech_and_tail(self):
        recorder = Mock()
        # 160 ms of speech followed by silence.
        recorder.record.side_effect = [np.full((80, 2), 0.1)] * 2 + [np.zeros((80, 2))] * 4
        clicked = threading.Event()
        clicked.set()
        audio = self.solver._record_until_quiet(recorder, 1000, 3.8, clicked)
        self.assertEqual(audio.shape, (480, 2))
        self.assertEqual(recorder.record.call_count, 6)

    def test_pause_inside_word_does_not_truncate_audio(self):
        speech = np.full((80, 2), 0.1)
        silence = np.zeros((80, 2))
        recorder = Mock()
        recorder.record.side_effect = [speech] * 2 + [silence] * 2 + [speech] * 2 + [silence] * 4
        clicked = threading.Event()
        clicked.set()
        audio = self.solver._record_until_quiet(recorder, 1000, 3.8, clicked)
        self.assertEqual(audio.shape[0], 800)

    def test_silence_waits_for_recording_cap(self):
        recorder = Mock()
        recorder.record.side_effect = lambda numframes: np.zeros((numframes, 2))
        clicked = threading.Event()
        clicked.set()
        audio = self.solver._record_until_quiet(recorder, 1000, 2.0, clicked)
        self.assertEqual(audio.shape[0], 2000)

    def test_confident_word_decodes_once(self):
        self.solver._decode = Mock(return_value=("apple", -0.2))
        self.assertEqual(self.solver.transcribe(Path("fake.wav"), ["apple"]), ("apple", "apple"))
        self.solver._decode.assert_called_once()
        self.assertTrue(self.solver.last_transcription_confident)

    def test_uncertain_word_still_gets_verified(self):
        self.solver._decode = Mock(side_effect=[("apple", -1.2), ("apply", -1.3)])
        _, heard = self.solver.transcribe(Path("fake.wav"), ["apple", "apply"])
        self.assertEqual(self.solver._decode.call_count, 2)
        self.assertEqual(heard, "")

    def test_agreed_new_word_does_not_need_replay(self):
        self.solver._decode = Mock(return_value=("banana", -0.3))
        _, heard = self.solver.transcribe(Path("fake.wav"), ["apple"])
        self.assertEqual(heard, "banana")
        self.assertTrue(self.solver.last_transcription_confident)

    def test_short_startup_sound_cannot_end_recording_before_one_second(self):
        self.solver.cfg["audio_min_record_seconds"] = 1.0
        recorder = Mock()
        frames = [np.full((80, 2), 0.1)] * 2 + [np.zeros((80, 2))] * 20
        recorder.record.side_effect = frames
        clicked = threading.Event()
        clicked.set()
        audio = self.solver._record_until_quiet(recorder, 1000, 2.4, clicked)
        self.assertGreaterEqual(audio.shape[0], 1000)

    def test_weak_agreement_on_unknown_word_still_needs_replay(self):
        self.solver._decode = Mock(return_value=("peace", -0.8))
        self.solver.transcribe(Path("fake.wav"), ["appeal"])
        self.assertFalse(self.solver.last_transcription_confident)

    def test_real_microphone_is_never_used_as_loopback_fallback(self):
        import types
        mic = types.SimpleNamespace(name="microphone", id="mic", isloopback=False)
        sc = types.SimpleNamespace(all_microphones=lambda **kw: [mic])
        self.assertIsNone(self.solver._find_loopback_device(sc, ["mic"], fallback_first_loopback=True))
