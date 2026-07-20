# 内网 CA 证书投放目录

把**公司内网根/中间 CA** 的证书文件放进本目录，构建时会被烘焙进沙箱镜像，让容器内
apt / curl / Python `ssl` / pip / requests / httpx / uv **全部**信任内网证书。

## 为什么需要

容器里有 **3 套互不相通的 TLS 信任源**，装一次 CA 并不能一劳永逸，Dockerfile 里分别处理：

| 信任源 | 谁在用 | 怎么修 |
|---|---|---|
| 系统 `/etc/ssl/certs` | apt、curl、Python 标准库 `ssl` | `update-ca-certificates` |
| certifi bundle | pip(vendored)、requests、httpx | 追加进 certifi 的 `cacert.pem` |
| webpki（编进二进制） | uv | `UV_NATIVE_TLS=1` 改用系统信任源 |

不装 CA 的后果不止装包失败——skill 用 requests/httpx 打内网监控/CMDB/告警 https API
会 `SSLCertVerificationError`，而这正是 SRE Agent 的主用途。

## 怎么用

1. 拿到 CA 证书。可直接从内网站点抓证书链：
   ```bash
   openssl s_client -showcerts -connect mirrors.tools.huawei.com:443 </dev/null 2>/dev/null \
     | awk '/BEGIN CERT/,/END CERT/' > corp-ca.crt
   ```
   （更稳妥是向 IT 索取公司根 CA。）
2. 放进本目录，**文件名必须以 `.crt` 结尾、内容为 PEM 格式**（`-----BEGIN CERTIFICATE-----`）。
   `update-ca-certificates` 只认 `.crt`；DER 格式需先
   `openssl x509 -inform der -in x.cer -out x.crt` 转换。
3. 正常构建即可（`bash deploy/sandbox/build-sandbox-image.sh v1`）。

## 空目录是合法状态

外网构建时本目录只有这个 README，Dockerfile 里那两层会自动 no-op（不写系统信任源、
不改 certifi），行为与不装 CA 完全一致。所以**不要**为了"干净"删掉本目录——
Dockerfile 的 `COPY ca/` 需要它常在（Dockerfile 无法条件化 COPY）。

## 注意

- 证书**不是**机密，但仍按公司规定判断能否入库；不想进 git 就构建前临时放、构建后删。
- 运行期 skill 若自己 `pip install` 了新的 certifi 到 `--target` 目录，那份新 certifi
  不含内网 CA——这是已知限制，需要时让脚本显式 `verify=` 指向 `/etc/ssl/certs/ca-certificates.crt`。
