# -*- mode: python ; coding: utf-8 -*-
# NIQ Desktop paketi. .env GÖMÜLMEZ: API anahtarı kullanıcının Ayarlar sayfasından girilir
# ve işletim sisteminin güvenli deposunda (Windows Credential Manager) saklanır.

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
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'matplotlib', 'numpy', 'pandas', 'scipy', 'PIL', 'cv2',
        'PyQt5', 'PyQt6', 'pytest', 'ruff',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='NIQ-Desktop',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=False,
)
