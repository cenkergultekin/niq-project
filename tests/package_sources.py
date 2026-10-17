"""Verify frozen source and static asset parity without extracting credentials."""
import argparse
import marshal
import types
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[1]


def normalized(code):
    return (code.co_code, tuple(normalized(v) if isinstance(v, types.CodeType) else v for v in code.co_consts),
            code.co_names, code.co_varnames, code.co_flags, code.co_exceptiontable, code.co_argcount,
            code.co_posonlyargcount, code.co_kwonlyargcount, code.co_freevars, code.co_cellvars)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exe")
    args = parser.parse_args()
    archive = CArchiveReader(args.exe)
    pyz = archive.open_embedded_archive("PYZ.pyz")
    missing, stale = [], []
    desktop = Path(args.exe).stem == "NIQ-Desktop"
    required = ["backend.services.ai_accounts", "backend.services.ai_auth", "backend.services.ai_adapters",
                "backend.services.ai_service", "backend.core.utils", "backend.config.config",
                "sqlglot.dialects.sqlite", "anthropic", "jwt", "filelock"]
    if desktop:
        required += ["backend.routes.ai_accounts", "backend.routes.routes", "backend.routes.onboarding",
                     "backend.services.desktop_actions", "backend.services.analyst", "app"]
    else:
        required += ["backend.services.agent_api"]
    for name in required:
        if name not in pyz.toc:
            missing.append(name)
    sources = [*(ROOT / "backend").rglob("*.py"), *([ROOT / "app.py"] if desktop else [])]
    for path in sources:
        name = ".".join(path.relative_to(ROOT).with_suffix("").parts)
        if name.endswith(".__init__"):
            name = name.removesuffix(".__init__")
        if name not in pyz.toc:
            continue
        frozen = pyz.extract(name)
        actual = compile(path.read_text(encoding="utf-8"), frozen.co_filename, "exec")
        if normalized(frozen) != normalized(actual):
            stale.append(name)
    entry = "main_desktop" if desktop else "niq_mcp"
    frozen = marshal.loads(archive.extract(entry))
    actual = compile((ROOT / (entry + ".py")).read_text(encoding="utf-8"), frozen.co_filename, "exec")
    if normalized(frozen) != normalized(actual):
        stale.append(entry)
    if desktop:
        names = {n.replace("\\", "/"): n for n in archive.toc}
        for path in (ROOT / "frontend").rglob("*"):
            if path.is_file():
                name = path.relative_to(ROOT).as_posix()
                if name not in names or archive.extract(names[name]) != path.read_bytes():
                    stale.append(name)
    assert not any(n.endswith(".env") for n in archive.toc), "Environment file embedded"
    assert not missing and not stale, {"missing": missing, "stale": stale}
    print({"passed": True, "executable": Path(args.exe).name, "source_and_assets": "current"})


if __name__ == "__main__":
    main()
