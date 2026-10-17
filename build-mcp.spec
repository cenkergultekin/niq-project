# stdio MCP needs a console-subsystem executable, independent of Qt's GUI entry.
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(['niq_mcp.py'], pathex=[], binaries=[], datas=[],
             hiddenimports=['keyring.backends.Windows', 'sqlalchemy.dialects.mssql.pyodbc',
                            'sqlalchemy.dialects.postgresql.psycopg', 'sqlalchemy.dialects.mysql.pymysql',
                            'sqlalchemy.dialects.sqlite.pysqlite'] + collect_submodules('sqlglot'),
             excludes=['PySide6', 'tkinter', 'pytest', 'numpy', 'pandas', 'matplotlib'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='NIQ-MCP', console=True,
          icon='frontend/static/images/niq.ico')
