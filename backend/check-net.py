"""console + GLM 出站自检——用与后端完全相同的 env/httpx 复现（或排除）504 与 Connection error。

后端报 `MCP 注册表 list_servers 失败：504` / `Model glm-5.1: Connection error` 而同机
curl/test.py 是通的时，用本脚本一锤定音：它用 OPENOPS_* 环境变量 + httpx 默认配置（与后端
完全一致）依次自检：
  ① console `mcps/list/query`（带 OPENOPS_MCPREGISTRY_COOKIE）→ cookie 是否截断/失效、是否复现 504；
  ② 从 PG 读 sre_model_asset 里 glm 的 base_url（后端真正拨的地址）→ GET {base}/models 探连通。
     —— test.py 通只证明「正确 URL」通；② 证明「DB 里那个 URL」通不通。

用法（复用 run-backend.sh 已 export 的变量）：把末行 `exec "$PY" run.py` 临时改成
`exec "$PY" check-net.py`，跑 `bash run-backend.sh`，看完输出改回。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import httpx  # noqa: E402


def check_console() -> None:
    from infra.external.mcp_registry_client import console_cookie, console_tls_verify, http_trust_env  # 与后端同口径

    base = os.environ.get("OPENOPS_MCPREGISTRY_BASE_URL", "").strip()
    cookie = console_cookie("OPENOPS_MCPREGISTRY_COOKIE")  # 专属 > 共享 OPENOPS_CONSOLE_COOKIE
    print(f"[check-net]① console={base or '(未设 OPENOPS_MCPREGISTRY_BASE_URL，跳过)'}  "
          f"tls={console_tls_verify()!r}  trust_env={http_trust_env()}")
    if not base:
        return
    #  cookie 含 `;`，bash export 引号不当会被静默截断——长度+分段数即可识破（不打印值）
    print(f"[check-net]   cookie: len={len(cookie)} 段数={len([p for p in cookie.split(';') if p.strip()])}"
          f"{'（⚠未设，console 会拒绝）' if not cookie else ''}")
    url = f"{base.rstrip('/')}/obsv/agent/management/mcps/list/query"
    try:
        r = httpx.post(url, json={"page": 1, "page_size": 50, "source": "openops"},
                       headers={"Cookie": cookie} if cookie else {}, timeout=15,
                       verify=console_tls_verify(), trust_env=http_trust_env())
        print(f"[check-net]   HTTP {r.status_code}；响应前 300 字：{r.text[:300]}")
        print("[check-net]   → 200+code:0=通；504=复现（cookie 失效/截断或网关问题）；401/403=cookie 不被认")
    except Exception as e:  # noqa: BLE001 —— 原样暴露真实异常
        print(f"[check-net]   ❌ {type(e).__name__}: {e}")
        if "CERTIFICATE_VERIFY_FAILED" in str(e):
            print("[check-net]   → 内网证书不在 Python certifi CA（curl 用 Windows 证书库所以通）。三选一：\n"
                  "[check-net]     a) pip install truststore（run.py 自动用系统证书库，正解）\n"
                  "[check-net]     b) export OPENOPS_TLS_CA_FILE=<公司根CA.pem>\n"
                  "[check-net]     c) export OPENOPS_TLS_INSECURE=1（联调临时，等 curl -k）")


def check_glm() -> None:
    model_id = os.environ.get("OPENOPS_RUNTIME_MODEL", "glm-5.1")
    key = os.environ.get("OPENOPS_PLATFORM_GLM_API_KEY", "")
    print(f"[check-net]② GLM（DB 里 {model_id} 的 base_url，即后端真正拨的地址）")
    try:
        import psycopg

        from infra.db import DATABASE_URL

        with psycopg.connect(DATABASE_URL, connect_timeout=10) as conn:
            row = conn.execute("select base_url from sre_model_asset where model_id=%s", (model_id,)).fetchone()
    except Exception as e:  # noqa: BLE001
        print(f"[check-net]   ❌ 读 DB 失败：{type(e).__name__}: {e}")
        return
    if row is None:
        print(f"[check-net]   ❌ DB 里没有 model_id={model_id} 的资产")
        return
    stored = row[0]
    print(f"[check-net]   DB base_url = {stored!r}")
    if not stored:
        print("[check-net]   ❌ base_url 为空 → OpenAI 客户端会拨默认 api.openai.com（内网必挂）。UPDATE 之。")
        return
    if "bigmodel.cn" in stored:
        print("[check-net]   ❌ 还是 seed 的公网 bigmodel.cn（内网不可达）→ 这就是 Connection error 根因。UPDATE 之。")

    from app.model_gateway import _openai_base_url  # 与后端同一派生逻辑
    from infra.external.mcp_registry_client import http_trust_env

    api_base = _openai_base_url(stored)
    print(f"[check-net]   派生 base = {api_base}（后端在此基础上拼 /chat/completions）  trust_env={http_trust_env()}")
    try:
        r = httpx.get(f"{api_base.rstrip('/')}/models", headers={"Authorization": f"Bearer {key}"} if key else {},
                      timeout=15, trust_env=http_trust_env())
        print(f"[check-net]   GET /models → HTTP {r.status_code}；前 300 字：{r.text[:300]}")
        print("[check-net]   → 200=通（后端应能连）；401=key 错；ConnectError/TLS=URL 的 scheme/host/port 有误")
    except Exception as e:  # noqa: BLE001
        print(f"[check-net]   ❌ {type(e).__name__}: {e}")
        print("[check-net]   对照：ConnectError=host/port 不通；TLS/SSL 错=https 拨了 http 端口（改 http://）")


def _looks_html(r: "httpx.Response") -> bool:
    return "text/html" in str(r.headers.get("content-type", "")) or r.text.lstrip()[:9].lower().startswith("<!doctype")


def check_omodel() -> None:
    from infra.external.mcp_registry_client import console_tls_verify, http_trust_env

    raw = os.environ.get("OPENOPS_OMODEL_BASE_URL", "").strip()
    base = raw.split("#", 1)[0].rstrip("/")  # 剥浏览器地址栏的 #fragment（同 omodel_real._base）
    print(f"[check-net]③ oModel(umodel)={base or '(未设 OPENOPS_OMODEL_BASE_URL，跳过)'}"
          + ("（已剥 #fragment）" if raw and base != raw.rstrip("/") else ""))
    if not base:
        return
    from infra.external.mcp_registry_client import console_cookie

    cookie = console_cookie("OPENOPS_OMODEL_COOKIE")  # 专属 > 共享 OPENOPS_CONSOLE_COOKIE
    hdrs = {"Cookie": cookie} if cookie else {}
    print(f"[check-net]   cookie: {'len=' + str(len(cookie)) if cookie else '未设（29.7 workspace 端点匿名可用）'}")
    kw = {"timeout": 10, "verify": console_tls_verify(), "trust_env": http_trust_env(), "headers": hdrs}
    try:
        r = httpx.get(f"{base}/healthz", **kw)
        if _looks_html(r) or r.status_code >= 400:
            # 网关可能只路由 /api/v1/*（healthz 不暴露）——降为提示，决定性检查是下面的 workspaces
            print(f"[check-net]   （/healthz HTTP {r.status_code}"
                  f"{'·HTML' if _looks_html(r) else ''}——网关未暴露 healthz 属正常，以 /api/v1 为准）")
        else:
            print(f"[check-net]   GET /healthz → HTTP {r.status_code}；{r.text[:200]}")
        r = httpx.get(f"{base}/api/v1/workspaces", **kw)
        if _looks_html(r):
            print(f"[check-net]   ❌ /api/v1/workspaces 返回的是网页（HTTP {r.status_code}）——地址不是 API 根。\n"
                  "[check-net]   → 拿真 API 根：浏览器开 OModel 页面 → F12 → Network → 随便点个操作，\n"
                  "[check-net]     看它请求的 /api/v1/workspaces 完整 URL，去掉 /api/v1/... 即 API 根；或问同事。")
            return
        if r.status_code in (401, 403):
            print(f"[check-net]   ❌ GET /api/v1/workspaces → HTTP {r.status_code}——该部署要登录态。\n"
                  "[check-net]   → 浏览器开 OModel 页面 → F12 → Network → 任一 /api/v1 请求 → Request Headers\n"
                  "[check-net]     → 复制整个 Cookie 值 → export OPENOPS_OMODEL_COOKIE='<值>'（含分号务必加引号）")
            return
        print(f"[check-net]   GET /api/v1/workspaces → HTTP {r.status_code}", end="")
        items = (r.json() or {}).get("items", []) if r.status_code == 200 else []
        print(f"；workspaces={len(items)}" + (f"（如 {items[0].get('id')}）" if items else "（空，向导里创建即可）"))
        if items:  # 顺带验 resolve 的数据源（端点 4）
            ws = items[0].get("id")
            r = httpx.get(f"{base}/api/v1/workspaces/{ws}/projects", **kw)
            print(f"[check-net]   GET /{ws}/projects → HTTP {r.status_code}；前 200 字：{r.text[:200]}")
    except Exception as e:  # noqa: BLE001 —— 原样暴露真实异常
        print(f"[check-net]   ❌ {type(e).__name__}: {e}")
        if "CERTIFICATE_VERIFY_FAILED" in str(e):
            print("[check-net]   → 同 ① 的三选一（truststore / OPENOPS_TLS_CA_FILE / OPENOPS_TLS_INSECURE=1）")


def check_apptree() -> None:
    """④ 应用目录（初始化「从应用创建系统范围」选源）：与 apptree_client 完全同口径打一次真请求。

    对话框"空列表"三大来源一次照出：401/HTML=cookie 失效、404=enterprise/project 段错、
    200+status 非 OK / datas 空=账号（uesrId）没查到应用。
    """
    from infra.external.apptree_client import _map_rows, resolve_url
    from infra.external.mcp_registry_client import console_cookie, console_tls_verify, http_trust_env

    mode = os.environ.get("OPENOPS_APPTREE", "mock").strip().lower()
    url, how = resolve_url()  # 与后端同口径：URL 原样模式 / BASE+模板组装（整条 curl URL 直贴自动识别）
    print(f"[check-net]④ apptree={mode}  "
          + (f"端点[{how}]={url}" if url else "(未配 OPENOPS_APPTREE_URL / _BASE_URL" + ("——real 必配)" if mode == "real" else "，跳过)")))
    if mode != "real" or not url:
        return
    cookie = console_cookie("OPENOPS_APPTREE_COOKIE")
    w3 = os.environ.get("OPENOPS_APPTREE_USER_ID", "").strip()
    print(f"[check-net]   cookie: {'len=' + str(len(cookie)) if cookie else '未设'}  "
          f"uesrId={w3 or '(⚠未设 OPENOPS_APPTREE_USER_ID——将用登录态 user_id，联调 mock 头不是 W3 账号会查空)'}")
    try:
        r = httpx.post(url, json={"uesrId": w3 or "unset"}, timeout=10,
                       headers={"Cookie": cookie} if cookie else {},
                       verify=console_tls_verify(), trust_env=http_trust_env())
        if _looks_html(r):
            print(f"[check-net]   ❌ 返回 HTML（HTTP {r.status_code}）——cookie 失效或 BASE_URL 填成前端页")
            return
        if r.status_code >= 400:
            print(f"[check-net]   ❌ HTTP {r.status_code}：{r.text[:200]}")
            return
        payload = r.json() or {}
        status = str(payload.get("status", "")).upper()
        if status and status != "OK":
            print(f"[check-net]   ❌ HTTP {r.status_code} 但信封 status={payload.get('status')}：{str(payload.get('message', ''))[:200]}\n"
                  "[check-net]   → 常见原因：BASE_URL 贴了整条 URL 导致路径双拼（现已自动截回，重跑核对上面 base=）、"
                  "enterprise/project 段不对、该账号无权限")
            return
        rows = _map_rows(payload.get("data") or {})
        print(f"[check-net]   POST → HTTP {r.status_code}  status={payload.get('status')}  "
              f"rows={len(rows)}" + (f"（如 {rows[0]['name']} {rows[0]['app_id']}）" if rows else "（空——该账号无应用或 uesrId 不对）"))
    except Exception as e:  # noqa: BLE001
        print(f"[check-net]   ❌ {type(e).__name__}: {e}")


def check_skillhub() -> None:
    """⑤ Skill Hub（skills/list/query，同 console 网关）：与 skill_hub_client 完全同口径打一次真请求。
    401/HTML=cookie 失效；code!=0=业务错误（信封 message 说明原因）；items=0=注册表里没有 source=openops 的 skill。"""
    from infra.external.mcp_registry_client import console_api_prefix, console_cookie, console_tls_verify, http_trust_env
    from infra.external.skill_hub_client import skillhub_base

    mode = os.environ.get("OPENOPS_SKILLHUB", "mock").strip().lower()
    base = skillhub_base()
    miss = "(未配 OPENOPS_SKILLHUB_BASE_URL / _MCPREGISTRY_BASE_URL" + ("——real 必配)" if mode == "real" else "，跳过)")
    print(f"[check-net]⑤ skillhub={mode}  base={base or miss}")
    if mode != "real" or not base:
        return
    cookie = console_cookie("OPENOPS_SKILLHUB_COOKIE")
    print(f"[check-net]   cookie: {'len=' + str(len(cookie)) if cookie else '未设（console 面大概率 401）'}")
    url = f"{base}{console_api_prefix()}/skills/list/query"
    try:
        r = httpx.post(url, json={"page": 1, "page_size": 50, "source": "openops"}, timeout=15,
                       headers={"Cookie": cookie} if cookie else {},
                       verify=console_tls_verify(), trust_env=http_trust_env())
        if _looks_html(r):
            print(f"[check-net]   ❌ 返回 HTML（HTTP {r.status_code}）——cookie 失效或 BASE_URL 错")
            return
        if r.status_code >= 400:
            print(f"[check-net]   ❌ HTTP {r.status_code}：{r.text[:200]}")
            return
        body = r.json() or {}
        if int(body.get("code", -1)) != 0:
            print(f"[check-net]   ❌ HTTP {r.status_code} 但业务 code={body.get('code')}：{str(body.get('message', ''))[:200]}")
            return
        items = (body.get("data") or {}).get("items") or []
        print(f"[check-net]   POST /skills/list/query → HTTP {r.status_code}  code=0  items={len(items)}"
              + (f"（如 {items[0].get('name')} · {items[0].get('skill_id')}）" if items else "（空——注册表无 source=openops 的 skill）"))
    except Exception as e:  # noqa: BLE001
        print(f"[check-net]   ❌ {type(e).__name__}: {e}")


if __name__ == "__main__":
    check_console()
    print()
    check_glm()
    print()
    check_omodel()
    print()
    check_apptree()
    print()
    check_skillhub()
