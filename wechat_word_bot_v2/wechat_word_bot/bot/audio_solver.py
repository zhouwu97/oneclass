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
        import logging
        import soundcard as sc

        explicit = str(self.cfg.get("audio_loopback_name", "")).strip()
        if explicit:
            try:
                return sc.get_microphone(explicit, include_loopback=True)
            except Exception as exc:  # noqa: BLE001
                logging.getLogger("wordbot").warning(
                    "指定的录音设备 %r 不可用，退回系统默认扬声器回环：%s",
                    explicit,
                    exc,
                )
        speaker = sc.default_speaker()
        # SoundCard 官方接口支持把扬声器作为 loopback microphone 获取。
        return sc.get_microphone(str(speaker.name), include_loopback=True)

    def list_devices(self) -> list[str]:
        import soundcard as sc

        return [f"{m.name} | loopback={getattr(m, 'isloopback', False)}" for m in sc.all_microphones(include_loopback=True)]

    def record_after_click(self, click_speaker: Callable[[], None]) -> Path:
        import soundfile as sf

        from .config import speed_settings

        rate = int(self.cfg.get("audio_sample_rate", 48000))
        seconds = float(speed_settings(self.cfg)["record_seconds"])
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
        backend = str(self.cfg.get("asr_backend", "local")).lower().strip()
        if backend == "api":
            raw = self._transcribe_api(audio_path, vocabulary)
        elif backend == "local":
            raw = self._transcribe_local(audio_path, vocabulary)
        else:
            raise ValueError("asr_backend 只能是 local 或 api")
        pieces = re.findall(r"[A-Za-z][A-Za-z'-]+", raw)
        heard = clean_word(max(pieces, key=len) if pieces else raw)
        return raw, heard

    def _transcribe_local(self, audio_path: Path, vocabulary: list[str]) -> str:
        from faster_whisper import WhisperModel

        from .config import speed_settings

        settings = speed_settings(self.cfg)
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
            beam_size=int(settings["beam_size"]),
            condition_on_previous_text=False,
            initial_prompt=prompt,
            vad_filter=bool(self.cfg.get("whisper_vad", True)),
        )
        return " ".join(seg.text for seg in segments).strip()

    def _transcribe_api(self, audio_path: Path, vocabulary: list[str]) -> str:
        import requests

        import os

        api = self.cfg.get("api") or {}
        base_url = str(api.get("asr_base_url") or api.get("base_url") or "").rstrip("/")
        env_name = str(api.get("api_key_env") or "ONECLASS_API_KEY")
        api_key = str(api.get("api_key") or os.environ.get(env_name) or "")
        if not base_url or not api_key:
            raise RuntimeError(
                f"asr_backend=api 需要 config.yaml 的 api.base_url 和 api.api_key（或环境变量 {env_name}）"
            )
        prompt = "Possible vocabulary: " + ", ".join(vocabulary[:250]) if vocabulary else None
        with audio_path.open("rb") as handle:
            response = requests.post(
                f"{base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (audio_path.name, handle, "audio/wav")},
                data={
                    "model": str(self.cfg.get("asr_api_model", "whisper-1")),
                    "language": "en",
                    **({"prompt": prompt} if prompt else {}),
                },
                timeout=float(api.get("timeout", 90)),
            )
        if response.status_code != 200:
            raise RuntimeError(f"语音 API HTTP {response.status_code}：{response.text[:200]}")
        text = str(response.json().get("text", "")).strip()
        if not text:
            raise RuntimeError("语音 API 返回空文本")
        return text
