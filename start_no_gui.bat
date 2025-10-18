@echo off
echo 启动电梯调度算法模拟器（无GUI）...

:: 检查Python是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误: 未找到Python。请先安装Python 3.7或更高版本。
    pause
    exit /b 1
)

:: 安装依赖
echo 正在安装依赖...
pip install -r requirements.txt >nul 2>&1

:: 启动模拟器服务器
echo 正在启动模拟器服务器...
start cmd /k "python -m elevator_saga.server --host 127.0.0.1 --port 8001"

:: 等待服务器启动
timeout /t 2 /nobreak >nul

:: 启动优化算法（SAGA智能算法）
echo 正在启动SAGA智能算法控制器...
start cmd /k "python -m elevator.smart_algorithm"

echo 启动完成！
echo - 模拟器服务器已启动在 http://127.0.0.1:8001
echo - SCAN算法控制器已启动
echo - 如需使用UI界面，请在浏览器中访问 http://127.0.0.1:8001/ui
pause
