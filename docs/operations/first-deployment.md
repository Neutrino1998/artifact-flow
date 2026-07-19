# 首次生产部署

本页假设[生产主机准备](host-preparation.md)已经完成，并且你拿到与目标架构一致的 Release transport archive。

示例版本为 `1.4.0`，安装根目录为 `/opt/artifactflow`。

## 1. 校验并解包

将 `.tar` 和 `.sha256` 放在同一目录：

```bash
sha256sum -c artifactflow-release-1.4.0-amd64.tar.sha256
tar xf artifactflow-release-1.4.0-amd64.tar
```

解包后目录包含 `afctl`、`manifest.json` 和若干压缩 artifact。不要单独替换其中某个文件；manifest 是整个目录的机器契约。

## 2. 初始化站点

内网静态 TLS：

```bash
sudo ./1.4.0/afctl --root /opt/artifactflow site init --preset intranet
```

公网 ACME：

```bash
sudo ./1.4.0/afctl --root /opt/artifactflow site init --preset public
```

该命令创建 `control/`、`.artifactflow/` 和 `bin/`，生成随机 JWT、Credential 和 bundled PostgreSQL 密钥。已有旧版 `deploy/.env` 时会拒绝初始化，必须走[旧版迁移](releases.md#从-fleet-v1-迁移)。

## 3. 配置站点

编辑：

```bash
sudo vi /opt/artifactflow/control/site.toml
sudo vi /opt/artifactflow/control/.env
```

默认 preset 使用：

```toml
executor = "local"
infra = "bundled"
sandbox_runtime = "runsc"
backend_replicas = 2
```

确认 `scratch_root` 与 commissioning 时准备的挂载点完全相同。

如果使用 external PostgreSQL/Redis：

1. 把 `infra` 改为 `external`；
2. 在 `.env` 中把数据库和 Redis URL 改成外部地址；
3. 删除并非必需，但可移除不再使用的 `POSTGRES_*`；
4. 从目标机先验证网络和凭证。

在 `.env` 中补齐至少一个实际使用的模型供应商 API Key，以及 Tool/MCP 所需的 `TOOL_SECRET_*`。全部应用和站点字段见[应用与站点配置](../configuration/runtime.md)。

## 4. 放置 TLS 材料

静态 TLS：

```bash
sudo install -m 0644 server.crt /opt/artifactflow/control/certs/server.crt
sudo install -m 0600 server.key /opt/artifactflow/control/certs/server.key
```

`server.crt` 应包含 leaf 与 intermediate 完整链。

ACME 则确认 `.env` 中 `AF_DOMAIN`、`AF_ACME_EMAIL` 已替换，并且公网 DNS、80/443 已生效。

## 5. Doctor 与 Plan

```bash
sudo ./1.4.0/afctl --root /opt/artifactflow site validate
sudo ./1.4.0/afctl --root /opt/artifactflow doctor
sudo ./1.4.0/afctl --root /opt/artifactflow plan apply ./1.4.0
```

- `site validate` 检查严格 TOML、环境变量和 capability 组合；
- `doctor` 检查 Docker/Compose、runsc、scratch mount 和静态证书；
- `plan` 校验目标 Release 并打印动作，但不拿 mutation lock、不创建 Release、不加载镜像。

任何一步失败都先修复主机或配置，不要绕过检查直接运行 Compose。

## 6. Apply

```bash
sudo ./1.4.0/afctl --root /opt/artifactflow apply ./1.4.0
```

Apply 会：

1. 校验并物化完整 Release；
2. 加载 manifest 指定的精确镜像；
3. 开启维护页；
4. 启动 bundled infra（如启用）；
5. 执行数据库 migration 和 config reconcile；
6. Reconcile Backend、Frontend 和 Caddy；
7. 通过 Caddy 等待 `/health/ready`；
8. 原子更新 `.artifactflow/state.json`；
9. 关闭维护页。

健康检查成功前不会写 current state。失败时会尽力恢复上一个成功 Release；首次部署没有上一个 Release，错误会保留维护状态供 Operator 处理。

## 7. 安装稳定 afctl 入口

只有 Apply 成功后才安装本次 bundle 自带的控制器：

```bash
sudo install -m 0755 ./1.4.0/afctl /opt/artifactflow/bin/afctl
```

Apply 不会自动覆盖正在使用的 `afctl`。升级时仍应优先使用新 bundle 自带的控制器执行该 bundle，成功后再更新稳定入口。

## 8. 创建管理员

先列出 Backend 容器：

```bash
docker ps \
  --filter label=com.docker.compose.project=artifactflow \
  --filter label=com.docker.compose.service=backend
```

选择任一健康 Backend 的 container ID：

```bash
docker exec -it <backend-container-id> python scripts/create_admin.py admin
```

交互输入符合密码策略的管理员密码。不要把密码放在 shell history 中。

## 9. 验收

```bash
sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow status
```

然后验证：

- 浏览器可以通过正式域名或内网地址打开登录页；
- `/health/live` 和 `/health/ready` 返回成功；
- 管理员可以登录；
- 用实际模型完成一次最小对话；
- 需要 Sandbox 的站点完成一次 `bash`/Artifact 回写测试；
- 管理界面能看到预期的 Backend 实例和版本。

首次验收完成后记录 Release ID、`state.json` generation、证书信息和备份基线，再进入[日常维护](maintenance.md)。
