from __future__ import annotations

try:
    import soundcard as sc
except ImportError as exc:
    raise SystemExit("请先运行：pip install -r requirements-audio.txt") from exc

print("默认扬声器：", sc.default_speaker().name)
print("可用录音/回环设备：")
for mic in sc.all_microphones(include_loopback=True):
    print(f"- {mic.name} | loopback={getattr(mic, 'isloopback', False)}")
