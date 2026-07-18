@echo off
chcp 65001 >nul
echo 🚀 Запуск бота...

REM Путь к папке скрипта (текущая директория .bat)
set "SCRIPT_DIR=%~dp0"

set "PYEXE_CMD="
set "PYEXE_PATH="

REM 1) python из PATH (проверяем, что это не заглушка Windows Store)
where python >nul 2>nul && (
    for /f "delims=" %%p in ('where python') do (
        echo %%p | findstr /i "WindowsApps" >nul
        if errorlevel 1 (
            REM Это не заглушка Windows Store, используем
            python --version >nul 2>nul
            if not errorlevel 1 set "PYEXE_CMD=python"
        )
    )
)

REM 2) py-лаунчер
if not defined PYEXE_CMD if not defined PYEXE_PATH (
    where py >nul 2>nul && set "PYEXE_CMD=py -3"
)

REM 3) Поиск в %LocalAppData%\Programs\Python\Python3*
if not defined PYEXE_CMD if not defined PYEXE_PATH (
    for /f "delims=" %%d in ('dir /b /ad "%LocalAppData%\Programs\Python" 2^>nul ^| findstr /i "^Python3"') do (
        if exist "%LocalAppData%\Programs\Python\%%d\python.exe" (
            set "PYEXE_PATH=%LocalAppData%\Programs\Python\%%d\python.exe"
            goto :run
        )
    )
)

REM 4) Поиск в %ProgramFiles%\Python*
if not defined PYEXE_CMD if not defined PYEXE_PATH (
    for /f "delims=" %%d in ('dir /b /ad "%ProgramFiles%\Python*" 2^>nul') do (
        if exist "%ProgramFiles%\%%d\python.exe" (
            set "PYEXE_PATH=%ProgramFiles%\%%d\python.exe"
            goto :run
        )
    )
)

if not defined PYEXE_CMD if not defined PYEXE_PATH (
    echo ❌ Python не найден!
    echo.
    echo Решение:
    echo 1. Установите Python 3.x с https://www.python.org/downloads/
    echo 2. При установке обязательно отметьте "Add Python to PATH"
    echo 3. Или укажите путь к Python вручную в start_bot.bat
    echo.
    echo Если Python уже установлен, но не найден:
    echo - Перезапустите командную строку после установки
    echo - Или добавьте Python в PATH вручную через настройки системы
    echo.
    pause
    exit /b 1
)

:run
REM Проверяем, что Python действительно работает
if defined PYEXE_CMD (
    echo Используем интерпретатор: %PYEXE_CMD%
    %PYEXE_CMD% --version >nul 2>nul
    if errorlevel 1 (
        echo ❌ Python найден, но не работает. Возможно, это заглушка из Microsoft Store.
        echo.
        echo Решение: Установите Python с https://www.python.org/downloads/
        echo При установке обязательно отметьте "Add Python to PATH"
        echo.
        pause
        exit /b 1
    )
    echo.
    echo 📦 Установка зависимостей...
    %PYEXE_CMD% -m pip install -r "%SCRIPT_DIR%requirements.txt" 2>&1
    if errorlevel 1 (
        echo.
        echo ❌ Ошибка при установке зависимостей!
        echo.
        echo Попробуйте установить зависимости вручную:
        echo   %PYEXE_CMD% -m pip install -r "%SCRIPT_DIR%requirements.txt"
        echo.
        pause
        exit /b 1
    )
    echo.
    echo ✅ Зависимости установлены
    echo.
    echo 🤖 Запуск бота...
    echo.
    cd /d "%SCRIPT_DIR%"
    %PYEXE_CMD% "%SCRIPT_DIR%bot.py"
    if errorlevel 1 (
        echo.
        echo ❌ Бот завершился с ошибкой (код: %errorlevel%)
        echo.
    )
) else (
    echo Используем интерпретатор: %PYEXE_PATH%
    "%PYEXE_PATH%" --version >nul 2>nul
    if errorlevel 1 (
        echo ❌ Python найден, но не работает.
        echo.
        pause
        exit /b 1
    )
    echo.
    echo 📦 Установка зависимостей...
    "%PYEXE_PATH%" -m pip install -r "%SCRIPT_DIR%requirements.txt" 2>&1
    if errorlevel 1 (
        echo.
        echo ❌ Ошибка при установке зависимостей!
        echo.
        echo Попробуйте установить зависимости вручную:
        echo   "%PYEXE_PATH%" -m pip install -r "%SCRIPT_DIR%requirements.txt"
        echo.
        pause
        exit /b 1
    )
    echo.
    echo ✅ Зависимости установлены
    echo.
    echo 🤖 Запуск бота...
    echo.
    cd /d "%SCRIPT_DIR%"
    "%PYEXE_PATH%" "%SCRIPT_DIR%bot.py"
    if errorlevel 1 (
        echo.
        echo ❌ Бот завершился с ошибкой (код: %errorlevel%)
        echo.
    )
)

echo.
echo Бот остановлен.
pause
