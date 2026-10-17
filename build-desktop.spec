# -*- mode: python ; coding: utf-8 -*-
# NIQ Desktop paketi. .env GÖMÜLMEZ: API anahtarı kullanıcının Ayarlar sayfasından girilir
# ve işletim sisteminin güvenli deposunda (Windows Credential Manager) saklanır.

from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ['main_desktop.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('frontend/templates', 'frontend/templates'),
        ('frontend/static', 'frontend/static'),
    ],
    hiddenimports=[
        'pyodbc',
        'sqlalchemy.dialects.mssql.pyodbc',
        'sqlalchemy.dialects.postgresql.psycopg',
        'sqlalchemy.dialects.mysql.pymysql',
        'sqlalchemy.dialects.sqlite.pysqlite',
        'psycopg',
        'pymysql',
        'keyring.backends.Windows',
        'win32ctypes',
        'waitress',
        'sqlglot',
        'paramiko',
        'openpyxl',
        'openai',
    ] + collect_submodules('sqlglot'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'matplotlib', 'numpy', 'pandas', 'scipy', 'PIL', 'cv2',
        'PyQt5', 'PyQt6', 'pytest', 'ruff',
    ],
    noarchive=False,
)

# Qt'nin Windows ICU API'si işletim sisteminden gelir. PATH'teki Poppler gibi
# araçların aynı adlı ICU derlemesi uyumsuz sembollerle QtCore açılışını bozar.
# İşletim sistemine ait bu DLL'i paket içine gömerek gölgeleme.
import sys

if sys.platform == 'win32':
    a.binaries = [entry for entry in a.binaries if entry[0].lower() != 'icuuc.dll']

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='NIQ-Desktop',
    icon='frontend/static/images/niq.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=False,
)
