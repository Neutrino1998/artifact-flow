# afctl 运维速查

所有命令默认在 install root 工作；推荐显式传 `--root /opt/artifactflow`。

```bash
afctl site init --preset intranet|public
afctl site migrate-v1 --preset intranet|public --sandbox-runtime runsc|runc
afctl site validate
afctl doctor
afctl plan apply BUNDLE|RELEASE_ID|current
afctl apply BUNDLE|RELEASE_ID|current [--keep-maintenance]
afctl plan rollback
afctl rollback
afctl status
afctl maintenance on [NOTE]|off|status
afctl config checkout DIR
afctl config apply [--id ID] DIR
```

## 目录

```text
/opt/artifactflow/
├── bin/
│   ├── afctl
│   └── artifactflow-autoheal
├── control/
│   ├── site.toml
│   ├── .env
│   ├── inventory.ini       # 仅实验性 Ansible executor
│   ├── auth/                # Backend 认证 Provider（目标机本地，只读挂载）
│   ├── certs/              # Caddy 入站 server.crt/server.key
│   ├── trust/
│   │   └── ca-certificates/ # Backend 出站 HTTPS 信任锚（*.crt）
│   ├── site/               # 品牌和欢迎提示的 frontend 静态内容
│   ├── caddy/              # 实验性多机派生 upstream
│   ├── maintenance/
│   └── autoheal/
└── .artifactflow/
    ├── releases/<id>/
    ├── hotfix-bundles/<id>/
    ├── mutation.lock
    └── state.json
```

`state.json` 是 active/previous 的唯一权威；没有 `current` symlink 或 `.fleet-state`。release 目录是完整 effective snapshot：app release 直接含 app/config/deploy，config release 继承 expected base 的 app/deploy 后再成为完整 snapshot。

release 目录不会作为可写 bind mount。欢迎提示、品牌等 frontend 静态内容写入
`control/site/`；在线通知写入共享数据库。实验性多机的 Caddy upstream 写入
`control/caddy/`，升级和 rollback 都不会覆盖它们。

企业统一认证配置写入 `control/auth/remote_bearer_userinfo.yaml`，由 release 和
backend 容器只读挂载到 `config/auth/`。目录为空即关闭 SSO；修改文件后执行
`apply current` 重建所有 Backend，使各副本在启动时读取同一份不可变配置。

内网 HTTPS Tool/MCP 使用的企业根 CA 或自签 leaf 放在
`control/trust/ca-certificates/*.crt`；该目录可以为空，此时只使用镜像默认公共 CA。
证书采用 `update-ca-certificates` 接受的 PEM `.crt` 格式，不要放私钥。
它与 `control/certs/server.crt` 的入站 Caddy 证书语义分离；修改后执行
`apply current`，所有 Backend 副本会在重建时更新系统信任库。

`plan` 永远只读。所有 release-changing apply/rollback/config apply 都使用同一 kernel lock 和 reconcile executor。成功探活前不写 state；失败时尝试恢复上一个成功 release，恢复失败则明确报错并保留维护页。

需要在新 release 健康后继续执行停机检查时，使用 `apply TARGET
--keep-maintenance`。apply 仍会先启用维护页，但成功写入 `state.json` 后不自动关闭；
若 reconcile 失败但成功恢复 last-known-good release，也继续保留维护页。检查结束后必须显式执行
`afctl maintenance off`。该 flag 不改变 rollback 或 config apply 的默认行为。

`afctl` 不安装 runsc、不格式化磁盘，也不修改 `/etc/fstab`。runsc/runc 与 scratch mount 应在 commissioning 阶段由主机镜像、配置管理或明确的主机 SOP 预置，`doctor/apply` 只检查并 loud-fail。

应用 release 不会顺便覆盖 `/opt/artifactflow/bin/afctl`。新版 bundle 自带的 `afctl` 用于执行该版 `plan/apply`；成功后再由 operator 显式安装到稳定路径。Ansible executor 保留为实验性路径，目前只接受 external PostgreSQL/Redis；完成真实多机物理验收前不属于 production-supported contract。

完整流程见 [`docs/operations/first-deployment.md`](../docs/operations/first-deployment.md)、
[`docs/operations/releases.md`](../docs/operations/releases.md) 与
[`docs/operations/maintenance.md`](../docs/operations/maintenance.md)。
