# -*- mode: python ; coding: utf-8 -*-
# Build: pyinstaller EVE_Price_Checker.spec

a = Analysis(
    ['eve_price_checker.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='EVE_Price_Checker',
    debug=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=False,
    icon='assets/icon.ico',
)
