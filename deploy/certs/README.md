# deploy/certs — intranet HTTPS 静态证书

`Caddyfile.intranet` 从这里读证书(compose 把本目录只读挂进 caddy 容器的
`/etc/caddy/certs/`)。目录里除本 README 外全部 gitignore,release 打包也排除
(`scripts/release.sh` 的 deploy tar excludes)——证书和 `.env` 一样属于目标机
本地材料,不从构建机分发。

放两个文件(名字固定,Caddyfile 按此引用):

| 文件 | 内容 |
|---|---|
| `server.crt` | **完整链** PEM:服务器叶子证书在前,中间 CA 依次拼接在后 |
| `server.key` | 对应私钥(PEM,无口令) |

**证书还没到位?先自签一张顶着** —— Caddy 硬引用这两个文件,缺文件配置加载就失败、
容器起不来。新机首发时公司测试中心的证书往往还没签发下来,用 `ensure-cert.sh` 生成一张
自签占位证书让整个栈先起来:

```bash
deploy/scripts/ensure-cert.sh                              # 占位证书,SAN=localhost
AF_CERT_HOSTS=af.corp.local,10.0.0.7 deploy/scripts/ensure-cert.sh   # 追加 SAN
```

**幂等且不覆盖**:两个 pem 已存在(非空)就整个跳过,永远不会盖掉真证书;`fleet.sh` 起
caddy 前已自动调它,手工 `docker compose up` 时自己先跑一次即可。真证书到位后覆盖两个 pem
再 `caddy reload`(见下方「换证」),零停机。占位证书是自签的,客户端会显示不受信任 —— 它
只负责「让 Caddy 能启动」,不是长期方案。

注意事项:

- **叶子证书单独一张不够**:部分浏览器/客户端不会自己补中间链,握手直接失败。
  向测试中心要链上所有中间证书,`cat server-leaf.pem intermediate.pem > server.crt`。
- 客户端(浏览器)需信任公司根 CA —— 这是客户端侧配置,与 Caddy 无关。
- **换证**:覆盖两个 pem 后 `docker compose -f deploy/docker-compose.intranet.yml
  exec caddy caddy reload --config /etc/caddy/conf/Caddyfile.intranet --adapter caddyfile`,
  零停机,不需要维护窗口。
- 本地(Mac)验证用自签证书即可:
  ```bash
  openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
    -keyout deploy/certs/server.key -out deploy/certs/server.crt \
    -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
  ```
