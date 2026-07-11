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
    from infra.external.mcp_registry_client import console_tls_verify, http_trust_env  # 与后端同一 TLS/代理口径

    base = os.environ.get("OPENOPS_MCPREGISTRY_BASE_URL", "").strip()
    cookie = os.environ.get("OPENOPS_MCPREGISTRY_COOKIE", "")
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


if __name__ == "__main__":
    check_console()
    print()
    check_glm()
