@echo off
REM Baut EVE_Price_Checker.exe (Onefile, ohne Konsole) nach dist\
python -m pip install -r requirements.txt -r requirements-dev.txt || exit /b 1
pyinstaller --noconfirm --clean EVE_Price_Checker.spec || exit /b 1
echo.
echo Fertig: dist\EVE_Price_Checker.exe
