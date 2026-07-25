"""Agent Studio facade 的两条不变式（切片边界的 CI 契约）。

这两条不变式此前只存在于 `src/studio/__init__.py` 的注释里。它们一旦被破坏，
症状都不是「studio 坏了」而是「**全仓测试红**」或「诡异 ImportError」——本文件把它们
变成响亮且定位明确的失败。改 facade 前请先读这里。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

SRC = pathlib.Path(__file__).resolve().parents[2] / "src"


def test_studio_package_resolves_to_src():
    """`import studio` 必须解析到 src/studio（正规包），而不是 tests/studio（测试目录）。

    背景：pyproject 的 `pythonpath = ["src", "tests"]` 让两者同名同时可见。PEP 420 规定
    带 __init__.py 的正规包胜过 namespace portion，所以今天必然选中 src/studio。
    这条断言把「哪天有人给 tests/studio 加了 __init__.py」从「诡异 ImportError」
    变成一句话点名的失败。
    """
    import studio

    assert pathlib.Path(studio.__file__).resolve().parts[-3] == "src", studio.__file__


def test_facade_import_stays_dependency_free():
    """`import studio` 不得拉起 agentscope / opentelemetry / fastapi / app / api / psycopg。

    这是 facade 铁律②的执行者，理由是硬的：`runtime.agentscope_runtime` **顶层**
    `import studio`。facade 只要顶层拉了 .middleware（import agentscope）或 .tracing
    （import opentelemetry.sdk）或 .service（→ app.* 链，且会与 runtime 成环），
    mock 档（pytest 默认、未装 agentscope）下 `import runtime.agentscope_runtime`
    就会 ModuleNotFoundError —— 症状是全量测试红，且错误信息指向 runtime 而非 studio。

    必须开**子进程**断言：本进程里 conftest 早已 import 了 fastapi/app/psycopg。
    """
    forbidden = {"agentscope", "opentelemetry", "fastapi", "app", "api", "psycopg"}
    code = (
        f"import sys; sys.path.insert(0, {str(SRC)!r}); import studio; "
        f"bad = sorted({{m.split('.')[0] for m in sys.modules}} & {forbidden!r}); "
        "assert not bad, 'facade 顶层拉起了重依赖: ' + repr(bad)"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_core_imports_studio_only_through_facade():
    """core 只准 `import studio`，不准 `from studio.xxx import ...`。

    切片内部的文件名与结构由 studio owner 随时改；只有 facade 的签名是对 core 的承诺。
    白名单**已清空**——切片抽离完成后，core 里不应再有任何 `from studio.xxx import`。
    若因迁移需要临时加回条目，请连同「何时清零」一起写在这里。
    """
    allow: set[str] = set()
    bad: list[str] = []
    for py in SRC.rglob("*.py"):
        rel = py.relative_to(SRC.parent).as_posix().removeprefix("backend/")
        if py.is_relative_to(SRC / "studio") or rel in allow:
            continue
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            s = line.strip()
            if s.startswith("from studio.") or s.startswith("from studio import"):
                bad.append(f"{rel}:{i}: {s}")
    assert not bad, "core 必须只经 facade 使用 studio：\n" + "\n".join(bad)
