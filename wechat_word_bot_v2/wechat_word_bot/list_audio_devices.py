from __future__ import annotations

try:
    from bot.audio_backend import soundcard_session
except ImportError as exc:
    raise SystemExit("请先运行：pip install -r requirements-audio.txt") from exc

try:
    with soundcard_session() as sc:
        print("默认扬声器：", sc.default_speaker().name)
        print("可用录音/回环设备：")
        for mic in sc.all_microphones(include_loopback=True):
            print(f"- {mic.name} | id={mic.id} | loopback={getattr(mic, 'isloopback', False)}")
except ImportError as exc:
    raise SystemExit("请先运行：pip install -r requirements-audio.txt") from exc
