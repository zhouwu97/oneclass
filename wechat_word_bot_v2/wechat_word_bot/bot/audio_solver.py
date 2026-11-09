from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np
from rapidfuzz import fuzz, process

from .config import BUNDLE_ROOT, ROOT
from .audio_backend import soundcard_module, soundcard_session
from .vision import clean_word


LOG = logging.getLogger("wordbot")
AUDIO_LOOPBACK_FIX_VERSION = "2026-06-17-id-fallback-v2"


class AudioSolver:
    def __init__(self, cfg: dict, work_dir: Path):
        self.cfg = cfg
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._model = None
        self._model_name = ""
        self._model_device = ""
        self._model_compute_type = ""
        self._loopback_mic = None
        self._loopback_lock = threading.Lock()
        self._preload_started = False
        self._model_lock = threading.Lock()
        self.last_recording_rms = 0.0
        self.last_transcription_confident = False

    def _record_until_quiet(self, recorder, rate: int, seconds: float, clicked: threading.Event) -> np.ndarray:
        """Keep a short tail after speech instead of waiting for the recording cap."""
        chunk_frames = max(1, int(rate * 0.08))
        max_frames = max(1, int(rate * seconds))
        chunks = []
        total = 0
        quiet_frames = 0
        speech_frames = 0
        threshold = float(self.cfg.get("audio_speech_rms", 0.001))
        tail = max(0.16, float(self.cfg.get("audio_silence_seconds", 0.32)))
        minimum_frames = min(max_frames, int(rate * float(self.cfg.get("audio_min_record_seconds", 1.0))))
        while total < max_frames:
            chunk = np.asarray(recorder.record(numframes=min(chunk_frames, max_frames - total)))
            if chunk.size == 0 or chunk.shape[0] == 0:
                break
            chunks.append(chunk)
            total += chunk.shape[0]
            mono = self._best_channel(chunk)
            rms = float(np.sqrt(np.mean(np.square(mono.astype(np.float64)))))
            if clicked.is_set() and rms >= threshold:
                speech_frames += chunk.shape[0]
                quiet_frames = 0
            elif speech_frames:
                quiet_frames += chunk.shape[0]
            if total >= minimum_frames and speech_frames >= rate * 0.16 and quiet_frames >= rate * tail:
                LOG.debug("语音结束，提前停止录音：%.2fs / 上限 %.2fs", total / rate, seconds)
                break
        return np.concatenate(chunks, axis=0) if chunks else np.empty((0, 1), dtype=np.float32)

    def _get_loopback(self):
        sc = soundcard_module()

        with self._loopback_lock:
            if self._loopback_mic is not None:
                return self._loopback_mic

        explicit = str(self.cfg.get("audio_loopback_name", "")).strip()
        if explicit:
            mic = self._get_microphone_by_tokens(sc, [explicit])
            if mic is not None:
                self._cache_loopback(mic)
                return mic
            LOG.warning(
                "配置的回环录音设备不可用：%s；改用系统默认扬声器。"
                "可运行 list_audio_devices.py 查看当前设备名称。",
                explicit,
            )
        speaker = sc.default_speaker()
        tokens = [getattr(speaker, "id", ""), getattr(speaker, "name", "")]
        mic = self._get_microphone_by_tokens(sc, tokens, fallback_first_loopback=True)
        if mic is not None:
            self._cache_loopback(mic)
            return mic
        available = "; ".join(self.list_devices())
        raise RuntimeError(
            "找不到可用的扬声器回环录音设备；"
            f"默认扬声器={getattr(speaker, 'name', '')!r}；"
            f"可用设备=[{available}]"
        )

    def _cache_loopback(self, mic) -> None:
        LOG.info("监听输出设备：%s | id=%s", getattr(mic, "name", ""), getattr(mic, "id", ""))
        with self._loopback_lock:
            self._loopback_mic = mic

    def _clear_loopback_cache(self) -> None:
        with self._loopback_lock:
            self._loopback_mic = None

    @staticmethod
    def _normalize_device_token(value: object) -> str:
        return re.sub(r"\s+", "", str(value or "")).casefold()

    @classmethod
    def _device_token_matches(cls, requested: str, candidate: object) -> bool:
        requested_norm = cls._normalize_device_token(requested)
        candidate_norm = cls._normalize_device_token(candidate)
        if not requested_norm or not candidate_norm:
            return False
        return requested_norm in candidate_norm or candidate_norm in requested_norm

    @classmethod
    def _find_loopback_device(
        cls,
        sc: Any,
        tokens: Iterable[str],
        *,
        fallback_first_loopback: bool = False,
    ):
        devices = list(sc.all_microphones(include_loopback=True))
        loopbacks = [
            mic for mic in devices if bool(getattr(mic, "isloopback", False))
        ]
        for token in tokens:
            token = str(token or "").strip()
            if not token:
                continue
            for mic in loopbacks:
                if cls._device_token_matches(token, getattr(mic, "id", "")):
                    return mic
                if cls._device_token_matches(token, getattr(mic, "name", "")):
                    return mic
        if fallback_first_loopback and loopbacks:
            LOG.warning(
                "未按设备标识匹配到回环录音设备，改用第一个可用回环设备：%s | id=%s",
                getattr(loopbacks[0], "name", ""),
                getattr(loopbacks[0], "id", ""),
            )
            return loopbacks[0]
        return None

    @classmethod
    def _get_microphone_by_tokens(
        cls,
        sc: Any,
        tokens: Iterable[str],
        *,
        fallback_first_loopback: bool = False,
    ):
        token_list = [str(token or "").strip() for token in tokens if str(token or "").strip()]
        for token in token_list:
            try:
                mic = sc.get_microphone(token, include_loopback=True)
                if bool(getattr(mic, "isloopback", False)):
                    return mic
            except Exception as exc:  # noqa: BLE001
                LOG.debug("按设备标识获取回环录音失败：%s -> %s", token, exc)
        return cls._find_loopback_device(
            sc,
            token_list,
            fallback_first_loopback=fallback_first_loopback,
        )

    def list_devices(self) -> list[str]:
        with soundcard_session() as sc:
            return [
                f"{mic.name} | id={getattr(mic, 'id', '')} | "
                f"loopback={getattr(mic, 'isloopback', False)}"
                for mic in sc.all_microphones(include_loopback=True)
            ]

    def describe_loopback_selection(self) -> list[str]:
        sc = soundcard_module()

        lines = [
            f"audio_loopback_fix: {AUDIO_LOOPBACK_FIX_VERSION}",
            f"audio_loopback_name: {self.cfg.get('audio_loopback_name', '')}",
        ]
        try:
            speaker = sc.default_speaker()
            lines.append(
                "default_speaker: "
                f"{getattr(speaker, 'name', '')} | id={getattr(speaker, 'id', '')}"
            )
        except Exception as exc:  # noqa: BLE001
            lines.append(f"default_speaker_error: {exc}")

        try:
            mic = self._get_loopback()
            lines.append(
                "selected_loopback: "
                f"{getattr(mic, 'name', '')} | id={getattr(mic, 'id', '')} | "
                f"loopback={getattr(mic, 'isloopback', False)}"
            )
        except Exception as exc:  # noqa: BLE001
            lines.append(f"selected_loopback_error: {exc}")

        lines.append("all_audio_devices:")
        lines.extend(f"- {line}" for line in self.list_devices())
        return lines

    @staticmethod
    def _best_channel(data: np.ndarray) -> np.ndarray:
        if data.ndim != 2:
            return np.asarray(data, dtype=np.float32).reshape(-1)
        if data.shape[1] == 0:
            return np.asarray([], dtype=np.float32)
        channels = np.asarray(data, dtype=np.float32)
        channel_rms = np.sqrt(np.mean(np.square(channels.astype(np.float64)), axis=0))
        index = int(np.argmax(channel_rms))
        LOG.debug("回环声道 RMS=%s，使用声道 %d", channel_rms.tolist(), index)
        return channels[:, index].reshape(-1)

    def _prepare_audio(self, data: np.ndarray, rate: int) -> np.ndarray:
        mono = self._best_channel(data)
        if mono.size == 0:
            return mono

        mono = mono.astype(np.float32, copy=False)
        mono = mono - float(np.mean(mono))
        original_duration = mono.size / max(1, rate)

        if bool(self.cfg.get("audio_trim_silence", True)):
            peak = float(np.max(np.abs(mono)))
            rms = float(np.sqrt(np.mean(np.square(mono.astype(np.float64)))))
            threshold = max(1e-5, peak * 0.025, rms * 0.20)
            active = np.flatnonzero(np.abs(mono) >= threshold)
            if active.size:
                margin = max(1, int(rate * 0.22))
                start = max(0, int(active[0]) - margin)
                end = min(mono.size, int(active[-1]) + margin + 1)
                if end - start >= int(rate * 0.18):
                    mono = mono[start:end]
                    LOG.info(
                        "音频静音裁剪：%.2fs -> %.2fs",
                        original_duration,
                        mono.size / max(1, rate),
                    )

        if bool(self.cfg.get("audio_normalize", True)) and mono.size:
            peak = float(np.max(np.abs(mono)))
            if 1e-5 < peak < 0.92:
                mono = mono * (0.92 / peak)

        return np.clip(mono, -1.0, 1.0)

    def record_after_click(self, click_speaker: Callable[[], None]) -> Path:
        import soundfile as sf

        from .config import speed_settings

        rate = int(self.cfg.get("audio_sample_rate", 48000))
        seconds = float(speed_settings(self.cfg)["record_seconds"])
        pre_click = float(self.cfg.get("audio_pre_click_delay", 0.18))
        output = self.work_dir / "last_dictation.wav"
        ready = threading.Event()
        clicked = threading.Event()
        result: dict[str, np.ndarray | Exception] = {}

        def worker() -> None:
            try:
                with soundcard_session():
                    mic = self._get_loopback()
                    with mic.recorder(samplerate=rate) as recorder:
                        ready.set()
                        if self.cfg.get("audio_auto_stop", True) and not self.cfg.get("audio_test_mode"):
                            data = self._record_until_quiet(recorder, rate, seconds, clicked)
                        else:
                            data = recorder.record(numframes=max(1, int(rate * seconds)))
                result["data"] = np.asarray(data)
            except Exception as exc:  # noqa: BLE001
                self._clear_loopback_cache()
                result["error"] = exc
                ready.set()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        if not ready.wait(timeout=5):
            raise TimeoutError("回环录音设备未能在 5 秒内就绪，请检查输出设备")
        if "error" in result:
            raise RuntimeError(f"回环录音启动失败：{result['error']}")
        time.sleep(max(0.0, pre_click))
        clicked.set()
        click_speaker()
        thread.join(timeout=seconds + 8)
        if thread.is_alive():
            raise TimeoutError("回环录音超时")
        if "error" in result:
            raise RuntimeError(f"回环录音失败：{result['error']}")

        raw_data = np.asarray(result.get("data"))
        if raw_data.size == 0:
            raise RuntimeError("录到的音频为空")

        mono = self._best_channel(raw_data)
        if not np.all(np.isfinite(mono)):
            raise RuntimeError("录音包含无效采样，请重新选择输出设备")
        self.last_recording_rms = float(np.sqrt(np.mean(np.square(mono.astype(np.float64)))))
        if self.last_recording_rms < float(self.cfg.get("audio_min_rms", 0.00001)):
            raise RuntimeError("未录到有效声音，请检查微信音量和监听的输出设备")

        data = mono if self.cfg.get("audio_test_mode") else self._prepare_audio(raw_data, rate)
        if data.size == 0:
            raise RuntimeError("音频预处理后为空")

        rms = float(np.sqrt(np.mean(np.square(data.astype(np.float64)))))
        LOG.info(
            "回环录音完成：recorded=%.2fs, saved=%.2fs, rms=%.6f",
            raw_data.shape[0] / max(1, rate),
            data.size / max(1, rate),
            rms,
        )
        sf.write(output, data, rate)
        return output

    def preload_model_async(self) -> None:
        if str(self.cfg.get("dictation_mode", "whisper")).lower().strip() != "whisper":
            return
        if self.cfg.get("asr_backend", "local") != "local":
            return
        if self._preload_started or self._model is not None:
            return
        self._preload_started = True

        def worker() -> None:
            try:
                self._load_model()
            except Exception as exc:  # noqa: BLE001
                LOG.warning("听写模型后台预热失败，将在首次听写时重试：%s", exc)
                self._preload_started = False

        threading.Thread(target=worker, name="oneclass-whisper-preload", daemon=True).start()

    def _resolve_runtime(self) -> tuple[str, str]:
        requested_device = str(self.cfg.get("whisper_device", "auto")).lower().strip()
        requested_compute = str(self.cfg.get("whisper_compute_type", "auto")).lower().strip()

        if requested_device == "auto":
            device = "cpu"
            try:
                import ctranslate2

                if ctranslate2.get_cuda_device_count() > 0:
                    device = "cuda"
            except Exception:  # noqa: BLE001
                device = "cpu"
        else:
            device = requested_device

        if requested_compute == "auto":
            compute = "float16" if device == "cuda" else "int8"
        else:
            compute = requested_compute
        return device, compute

    def _resolve_model_source(self) -> str:
        model_name = str(self.cfg.get("whisper_model", "small.en")).strip() or "small.en"
        explicit_path = str(self.cfg.get("whisper_model_path", "")).strip()
        candidates: list[Path] = []

        if explicit_path:
            explicit = Path(explicit_path)
            if not explicit.is_absolute():
                candidates.append(ROOT / explicit)
                candidates.append(BUNDLE_ROOT / explicit)
            else:
                candidates.append(explicit)

        for base in (ROOT, BUNDLE_ROOT):
            candidates.extend(
                [
                    base / "build_local_models" / model_name,
                    base / "models" / model_name,
                    base / "models" / f"faster-whisper-{model_name}",
                ]
            )

        for candidate in candidates:
            if (candidate / "config.json").exists() and (candidate / "model.bin").exists():
                LOG.info("优先使用包内听写模型：%s", candidate)
                return str(candidate)

        return model_name

    def _load_model(self):
        # Preloading and the first answer can arrive concurrently.
        with self._model_lock:
            return self._load_model_locked()

    def _load_model_locked(self):
        from faster_whisper import WhisperModel

        if self._model is not None:
            return self._model

        model_source = self._resolve_model_source()
        device, compute = self._resolve_runtime()
        cpu_threads = int(self.cfg.get("whisper_cpu_threads", 0))
        kwargs: dict[str, object] = {
            "device": device,
            "compute_type": compute,
        }
        if cpu_threads > 0:
            kwargs["cpu_threads"] = cpu_threads

        LOG.info(
            "加载听写模型：model=%s, device=%s, compute_type=%s",
            model_source,
            device,
            compute,
        )
        try:
            self._model = WhisperModel(model_source, **kwargs)
        except Exception as exc:
            if device != "cuda":
                raise
            LOG.warning("CUDA 模型加载失败，自动回退 CPU INT8：%s", exc)
            device = "cpu"
            compute = "int8"
            kwargs["device"] = device
            kwargs["compute_type"] = compute
            self._model = WhisperModel(model_source, **kwargs)

        self._model_name = model_source
        self._model_device = device
        self._model_compute_type = compute
        LOG.info(
            "听写模型已就绪：model=%s, device=%s, compute_type=%s",
            model_source,
            device,
            compute,
        )
        return self._model

    @staticmethod
    def _extract_candidates(raw: str) -> list[str]:
        result: list[str] = []
        for piece in re.findall(r"[A-Za-z][A-Za-z'-]*", raw):
            cleaned = clean_word(piece)
            if cleaned and cleaned not in result:
                result.append(cleaned)
        compact = clean_word(raw)
        if compact and compact not in result:
            result.append(compact)
        return result

    @staticmethod
    def _best_vocab_candidate(raw: str, vocabulary: list[str]) -> tuple[str, str, float]:
        candidates = AudioSolver._extract_candidates(raw)
        if not candidates:
            return "", "", 0.0
        if not vocabulary:
            heard = max(candidates, key=len)
            return heard, "", 0.0

        best_heard = ""
        best_word = ""
        best_score = -1.0
        for heard in candidates:
            match = process.extractOne(heard, vocabulary, scorer=fuzz.WRatio)
            if not match:
                continue
            word, score, _ = match
            score = float(score)
            if score > best_score or (score == best_score and len(heard) > len(best_heard)):
                best_heard = heard
                best_word = str(word)
                best_score = score
        return best_heard, best_word, max(0.0, best_score)

    @staticmethod
    def _mean_logprob(segments: Iterable[object]) -> float:
        values: list[float] = []
        weights: list[float] = []
        for segment in segments:
            value = getattr(segment, "avg_logprob", None)
            if value is None:
                continue
            duration = max(
                0.05,
                float(getattr(segment, "end", 0.0)) - float(getattr(segment, "start", 0.0)),
            )
            values.append(float(value))
            weights.append(duration)
        if not values:
            return -99.0
        return float(np.average(values, weights=weights))

    def _decode(self, audio_path: Path, *, beam_size: int, prompt: str | None) -> tuple[str, float]:
        model = self._load_model()
        segments, _ = model.transcribe(
            str(audio_path),
            language="en",
            task="transcribe",
            beam_size=max(1, beam_size),
            patience=float(self.cfg.get("whisper_patience", 1.4)),
            temperature=0.0,
            max_new_tokens=max(8, int(self.cfg.get("whisper_max_new_tokens", 16))),
            repetition_penalty=1.15,
            no_repeat_ngram_size=2,
            condition_on_previous_text=False,
            initial_prompt=prompt,
            vad_filter=bool(self.cfg.get("whisper_vad", True)),
        )
        segment_list = list(segments)
        raw = " ".join(str(segment.text) for segment in segment_list).strip()
        return raw, self._mean_logprob(segment_list)

    def transcribe(self, audio_path: Path, vocabulary: list[str]) -> tuple[str, str]:
        started = time.perf_counter()
        try:
            return self._transcribe(audio_path, vocabulary)
        finally:
            LOG.info("语音识别耗时：%.2fs", time.perf_counter() - started)

    def _transcribe(self, audio_path: Path, vocabulary: list[str]) -> tuple[str, str]:
        self.last_transcription_confident = False
        vocabulary = [clean_word(word) for word in vocabulary if clean_word(word)]
        from .config import speed_settings

        backend = str(self.cfg.get("asr_backend", "local")).strip().lower()
        if backend == "api":
            raw = self._transcribe_api(audio_path, vocabulary)
            heard, _, _ = self._best_vocab_candidate(raw, vocabulary)
            return raw, heard
        if backend != "local":
            raise ValueError("asr_backend 只能是 local 或 api")
        beam = int(speed_settings(self.cfg)["beam_size"])
        generic_prompt = str(
            self.cfg.get(
                "whisper_prompt",
                "A single clearly pronounced English vocabulary word.",
            )
        ).strip() or None

        raw1, logprob1 = self._decode(audio_path, beam_size=beam, prompt=generic_prompt)
        heard1, match1, score1 = self._best_vocab_candidate(raw1, vocabulary)
        LOG.info(
            "ASR pass1：raw=%r, heard=%r, vocab_match=%r, score=%.1f, logprob=%.3f",
            raw1,
            heard1,
            match1,
            score1,
            logprob1,
        )

        verify = bool(self.cfg.get("whisper_verify_pass", True))
        tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", raw1.lower())
        single_word = len(set(tokens)) == 1
        confident = single_word and bool(heard1) and logprob1 >= float(self.cfg.get("whisper_fast_logprob", -0.8))
        if confident and (heard1 == match1 or not vocabulary):
            self.last_transcription_confident = True
            LOG.info("听写首轮高置信度，跳过重复解码：%s", heard1)
            return raw1, heard1
        if not verify:
            self.last_transcription_confident = confident
            return raw1, heard1

        top_n = max(4, int(self.cfg.get("whisper_verify_candidates", 12)))
        seed = heard1 or clean_word(raw1)
        possible: list[str] = []
        if seed and vocabulary:
            possible = [
                str(item[0])
                for item in process.extract(seed, vocabulary, scorer=fuzz.WRatio, limit=top_n)
            ]
        if possible:
            verify_prompt = (
                "Exactly one English word is spoken. Possible spellings include: "
                + ", ".join(possible)
                + "."
            )
        else:
            verify_prompt = "Exactly one English vocabulary word is spoken."

        raw2, logprob2 = self._decode(
            audio_path,
            beam_size=max(beam, int(self.cfg.get("whisper_verify_beam_size", 3))),
            prompt=verify_prompt,
        )
        heard2, match2, score2 = self._best_vocab_candidate(raw2, vocabulary)
        LOG.info(
            "ASR pass2：raw=%r, heard=%r, vocab_match=%r, score=%.1f, logprob=%.3f",
            raw2,
            heard2,
            match2,
            score2,
            logprob2,
        )

        combined_raw = f"pass1={raw1!r}; pass2={raw2!r}"

        if heard1 and heard1 == heard2:
            self.last_transcription_confident = min(logprob1, logprob2) >= float(self.cfg.get("whisper_unknown_logprob", -0.55))
            return combined_raw, heard1

        if match1 and match1 == match2:
            heard = heard1 if (score1, logprob1) >= (score2, logprob2) else heard2
            LOG.info("ASR 双通道一致：%s", match1)
            return combined_raw, heard

        strong = float(self.cfg.get("whisper_strong_match_score", 96))
        weak = float(self.cfg.get("whisper_weak_match_score", 72))
        if match1 and score1 >= strong and score2 < weak:
            LOG.info("ASR 采用 pass1：pass2 明显较弱")
            return combined_raw, heard1
        if match2 and score2 >= strong and score1 < weak:
            LOG.info("ASR 采用 pass2：pass1 明显较弱")
            return combined_raw, heard2

        LOG.warning(
            "ASR 两次解码不一致：pass1=%r->%r(%.1f)，pass2=%r->%r(%.1f)；请求重听",
            heard1,
            match1,
            score1,
            heard2,
            match2,
            score2,
        )
        return combined_raw, ""

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
