from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from .vision import clean_word


class AudioSolver:
    def __init__(self, cfg: dict, work_dir: Path):
        self.cfg = cfg
        self.work_dir = work_dir
        self._model = None

    def _get_loopback(self):
        import soundcard as sc

        explicit = str(self.cfg.get("audio_loopback_name", "")).strip()
        if explicit:
            return sc.get_microphone(explicit, include_loopback=True)
        speaker = sc.default_speaker()
        # SoundCard 官方接口支持把扬声器作为 loopback microphone 获取。
        return sc.get_microphone(str(speaker.name), include_loopback=True)

    def list_devices(self) -> list[str]:
        import soundcard as sc

        return [f"{m.name} | loopback={getattr(m, 'isloopback', False)}" for m in sc.all_microphones(include_loopback=True)]

    def record_after_click(self, click_speaker: Callable[[], None]) -> Path:
        import soundfile as sf

        rate = int(self.cfg.get("audio_sample_rate", 48000))
        seconds = float(self.cfg.get("audio_record_seconds", 2.8))
        output = self.work_dir / "last_dictation.wav"
        ready = threading.Event()
        result: dict[str, np.ndarray | Exception] = {}

        def worker() -> None:
            try:
                mic = self._get_loopback()
                with mic.recorder(samplerate=rate) as rec:
                    ready.set()
                    data = rec.record(numframes=max(1, int(rate * seconds)))
                result["data"] = np.asarray(data)
            except Exception as exc:  # noqa: BLE001
                result["error"] = exc
                ready.set()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        ready.wait(timeout=5)
        if "error" in result:
            raise RuntimeError(f"回环录音启动失败：{result['error']}")
        time.sleep(0.18)
        click_speaker()
        thread.join(timeout=seconds + 8)
        if thread.is_alive():
            raise TimeoutError("回环录音超时")
        if "error" in result:
            raise RuntimeError(f"回环录音失败：{result['error']}")
        data = np.asarray(result.get("data"))
        if data.size == 0:
            raise RuntimeError("录到的音频为空")
        if data.ndim == 2:
            data = data.mean(axis=1)
        sf.write(output, data, rate)
        return output

    def transcribe(self, audio_path: Path, vocabulary: list[str]) -> tuple[str, str]:
        from faster_whisper import WhisperModel

        if self._model is None:
            self._model = WhisperModel(
                str(self.cfg.get("whisper_model", "small.en")),
                device=str(self.cfg.get("whisper_device", "cpu")),
                compute_type=str(self.cfg.get("whisper_compute_type", "int8")),
            )
        prompt = "Possible vocabulary: " + ", ".join(vocabulary[:250]) if vocabulary else None
        segments, _ = self._model.transcribe(
            str(audio_path),
            language="en",
            beam_size=5,
            condition_on_previous_text=False,
            initial_prompt=prompt,
            vad_filter=False,
        )
        raw = " ".join(seg.text for seg in segments).strip()
        pieces = re.findall(r"[A-Za-z][A-Za-z'-]+", raw)
        heard = clean_word(max(pieces, key=len) if pieces else raw)
        return raw, heard
