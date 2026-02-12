@echo off
chcp 65001 >nul
cls
echo.
echo ╔═══════════════════════════════════════════════════════════╗
echo ║                                                           ║
echo ║            УСТАНОВКА PYTHON И ЗАВИСИМОСТЕЙ                ║
echo ║                                                           ║
echo ╚═══════════════════════════════════════════════════════════╝
echo.

REM Проверка Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python не установлен!
    echo.
    echo ╔═══════════════════════════════════════════════════════╗
    echo ║                                                       ║
    echo ║         ИНСТРУКЦИЯ ПО УСТАНОВКЕ PYTHON                ║
    echo ║                                                       ║
    echo ╚═══════════════════════════════════════════════════════╝
    echo.
    echo 1. Откройте в браузере:
    echo    https://www.python.org/downloads/
    echo.
    echo 2. Скачайте Python 3.12 или новее
    echo.
    echo 3. При установке ОБЯЗАТЕЛЬНО отметьте:
    echo    ☑️ Add Python to PATH
    echo.
    echo 4. После установки перезапустите этот скрипт
    echo.
    echo Открыть сайт Python? (Y/N)
    choice /C YN /M "Выбор"
    if errorlevel 2 goto :end
    
    start https://www.python.org/downloads/
    goto :end
)

echo ✅ Python установлен:
python --version
echo.

REM Проверка pip
pip --version >nul 2>&1
if errorlevel 1 (
    echo ❌ pip не найден!
    echo.
    echo Переустановите Python с официального сайта
    pause
    exit /b 1
)

echo ✅ pip найден:
pip --version
echo.

REM Установка зависимостей
echo ╔═══════════════════════════════════════════════════════════╗
echo ║         УСТАНОВКА ЗАВИСИМОСТЕЙ БОТА                      ║
echo ╚═══════════════════════════════════════════════════════════╝
echo.
echo Это может занять несколько минут...
echo.

pip install --upgrade pip
echo.

pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo ❌ Ошибка установки!
    echo.
    echo Попробуйте установить вручную:
    echo   pip install aiogram fastapi uvicorn python-dotenv aiohttp
    echo.
    pause
    exit /b 1
)

echo.
echo ╔═══════════════════════════════════════════════════════════╗
echo ║                                                           ║
echo ║                   ВСЁ ГОТОВО! ✅                          ║
echo ║                                                           ║
echo ╚═══════════════════════════════════════════════════════════╝
echo.
echo Python и все зависимости установлены!
echo.
echo Теперь запустите бота:
echo   START_NO_DOCKER.bat
echo.

:end
pause
