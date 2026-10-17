@echo off
title NIQ Desktop - Build Creator
color 0A

echo.
echo ================================================================
echo                NIQ Desktop Build Creator
echo ================================================================
echo.

echo NIQ Desktop olusturuluyor...
echo 🖥️  Native masaustu uygulamasi olarak build ediliyor...
pyinstaller build-desktop.spec --clean --noconfirm
if %errorlevel% neq 0 (
    echo ❌ PyInstaller hatasi!
    pause
    exit /b 1
)
echo ✅ Desktop executable basariyla olusturuldu
echo MCP stdio sunucusu olusturuluyor...
pyinstaller build-mcp.spec --clean --noconfirm
if %errorlevel% neq 0 exit /b 1

echo.
echo Dosya boyutlari kontrol ediliyor...
for %%f in (dist\NIQ-Desktop.exe) do echo    NIQ-Desktop.exe: %%~zf bytes
for %%f in (dist\NIQ-MCP.exe) do echo    NIQ-MCP.exe: %%~zf bytes

echo.
echo ================================================================
echo                        TAMAMLANDI!
echo ================================================================
echo.
echo 📦 Dosya:
echo    - dist\NIQ-Desktop.exe    (Native Desktop App)
echo    - dist\NIQ-MCP.exe        (MCP stdio server)
echo.
echo 🚀 Kullanim:
echo    - Cift tikla: NIQ masaustu uygulamasi acilir
echo    - Gelistirici: http://127.0.0.1:5000 (browser)
echo.
echo ✨ Ozellikler:
echo    - PySide6 Qt WebEngine
echo    - Manrope ve Poppins fontlari
echo    - Profesyonel renk paleti
echo    - Menu bar ve kisa yollar
echo    - Tam ekran modu (F11)
echo    - Gelistirici araclari (F12)
echo.
pause
