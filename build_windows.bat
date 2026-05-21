@echo off
REM ============================================
REM ChangeSnap Windows 打包脚本
REM 输出: dist\ChangeSnap.exe (单文件，包含 Python 运行时及所有依赖)
REM
REM 使用方法:
REM   1. 在本机安装 Python 3.11+ (勾选 "Add Python to PATH")
REM   2. 双击运行本脚本
REM   3. 等待 5-10 分钟
REM   4. 在 dist\ 目录找到 ChangeSnap.exe
REM
REM ChangeSnap.exe 是独立可执行文件，复制到任意 Windows 10/11 即可运行
REM ============================================

setlocal
set SCRIPT_DIR=%~dp0
cd /d %SCRIPT_DIR%

echo === ChangeSnap Windows 打包 ===
echo.

REM 1. 虚拟环境 (隔离包环境，不污染系统 Python)
if not exist "venv" (
    echo [1/5] 创建虚拟环境...
    python -m venv venv
)
if not exist "venv\Scripts\python.exe" (
    echo [错误] 虚拟环境创建失败，请检查 Python 是否安装并添加到 PATH
    pause
    exit /b 1
)

REM 2. 安装依赖
echo [2/5] 安装依赖...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip -q
pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo [错误] 依赖安装失败，请检查网络连接
    pause
    exit /b 1
)

REM 3. 清理旧构建
echo [3/5] 清理旧构建...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

REM 4. 获取已安装的 imageio-ffmpeg 中的 ffmpeg 路径
echo [4/5] 定位 ffmpeg...
set FFMPEG_PATH=
python -c "import imageio_ffmpeg; import os; d=os.path.join(os.path.dirname(imageio_ffmpeg.__file__),'binaries'); print(next((os.path.join(d,f) for f in os.listdir(d) if f.startswith('ffmpeg-')),''))" > _ffmpeg_path.txt
set /p FFMPEG_PATH=<_ffmpeg_path.txt
del _ffmpeg_path.txt

REM 5. PyInstaller 打包
echo [5/5] PyInstaller 打包 (onefile, ~80MB)...

if "%FFMPEG_PATH%"=="" (
    echo [警告] 未找到 imageio-ffmpeg 二进制，ffmpeg 将从 PATH 查找
    pyinstaller --onefile --windowed ^
        --name "变更报告助手" ^
        --icon "icon.ico" ^
        --add-data "config.yaml;." ^
        --hidden-import PySide6.QtWidgets ^
        --hidden-import PySide6.QtCore ^
        --hidden-import PySide6.QtGui ^
        --hidden-import mss ^
        --hidden-import PIL ^
        --hidden-import PIL.ImageGrab ^
        --hidden-import docx ^
        --hidden-import docx.opc ^
        --hidden-import docx.oxml ^
        --hidden-import openpyxl ^
        --hidden-import yaml ^
        --hidden-import pynput ^
        --hidden-import pynput.keyboard ^
        --hidden-import numpy ^
        --hidden-import imageio ^
        --hidden-import imageio_ffmpeg ^
        --hidden-import json ^
        --collect-all imageio ^
        --collect-all imageio_ffmpeg ^
        --copy-metadata imageio ^
        --copy-metadata imageio_ffmpeg ^
        --copy-metadata numpy ^
        --copy-metadata Pillow ^
        --copy-metadata mss ^
        --exclude-module tkinter ^
        --exclude-module matplotlib ^
        --exclude-module IPython ^
        --exclude-module jupyter ^
        --clean ^
        --noconfirm ^
        app.py
) else (
    pyinstaller --onefile --windowed ^
        --name "变更报告助手" ^
        --icon "icon.ico" ^
        --add-data "config.yaml;." ^
        --add-binary "%FFMPEG_PATH%;imageio_ffmpeg/binaries" ^
        --hidden-import PySide6.QtWidgets ^
        --hidden-import PySide6.QtCore ^
        --hidden-import PySide6.QtGui ^
        --hidden-import mss ^
        --hidden-import PIL ^
        --hidden-import PIL.ImageGrab ^
        --hidden-import docx ^
        --hidden-import docx.opc ^
        --hidden-import docx.oxml ^
        --hidden-import openpyxl ^
        --hidden-import yaml ^
        --hidden-import pynput ^
        --hidden-import pynput.keyboard ^
        --hidden-import numpy ^
        --hidden-import imageio ^
        --hidden-import imageio_ffmpeg ^
        --hidden-import json ^
        --collect-all imageio ^
        --collect-all imageio_ffmpeg ^
        --copy-metadata imageio ^
        --copy-metadata imageio_ffmpeg ^
        --copy-metadata numpy ^
        --copy-metadata Pillow ^
        --copy-metadata mss ^
        --exclude-module tkinter ^
        --exclude-module matplotlib ^
        --exclude-module IPython ^
        --exclude-module jupyter ^
        --clean ^
        --noconfirm ^
        app.py
)

if %errorlevel% neq 0 (
    echo.
    echo [错误] PyInstaller 打包失败
    pause
    exit /b 1
)

echo.
echo ========================================
echo   打包完成！
echo   输出: %SCRIPT_DIR%dist\ChangeSnap.exe
echo.
echo   将此文件复制到任意 Windows 10/11 电脑
echo   双击即可运行，无需安装 Python 或任何包
echo ========================================
echo.

endlocal
