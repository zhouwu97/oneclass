@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title OneClass 背单词

set "PYEXE=%~dp0.venv\Scripts\python.exe"

if not exist "%PYEXE%" (
    echo [OneClass] 首次运行：创建 Python 虚拟环境...
    py -3.11 -m venv .venv >nul 2>nul
    if not exist "%PYEXE%" (
        python -m venv .venv
    )
    if not exist "%PYEXE%" (
        echo [OneClass] 创建虚拟环境失败，请先安装 Python 3.10 或 3.11。
        pause
        exit /b 1
    )
)

"%PYEXE%" -c "import pyautogui, mss, yaml, rapidocr, soundcard, faster_whisper" >nul 2>nul
if errorlevel 1 (
    echo [OneClass] 首次运行：安装核心及音频依赖...
    "%PYEXE%" -m pip install -r requirements-audio.txt --trusted-host pypi.org --trusted-host files.pythonhosted.org
    if errorlevel 1 (
        echo [OneClass] 依赖安装失败，请检查网络后重试。
        pause
        exit /b 1
    )
)

if "%~1"=="cli" (
    echo [OneClass] 启动命令行模式... 紧急停止可将鼠标移至屏幕左上角或按 Ctrl+C。
    "%PYEXE%" main.py
) else (
    echo [OneClass] 启动图形界面（可在界面中选择模型、速度与 API）...
    "%PYEXE%" gui.py
)

echo.
echo [OneClass] 已退出。
pause
