# afctl 运维速查

所有命令默认在 install root 工作；推荐显式传 `--root /opt/artifactflow`。

```bash
afctl site init --preset intranet|public
afctl site migrate-v1 --preset intranet|public --sandbox-runtime runsc|runc
afctl site validate
afctl doctor
afctl plan apply BUNDLE|RELEASE_ID|current
afctl apply BUNDLE|RELEASE_ID|current
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
│   ├── certs/
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

`plan` 永远只读。所有 release-changing apply/rollback/config apply 都使用同一 kernel lock 和 reconcile executor。成功探活前不写 state；失败时尝试恢复上一个成功 release，恢复失败则明确报错并保留维护页。

`afctl` 不安装 runsc、不格式化磁盘，也不修改 `/etc/fstab`。runsc/runc 与 scratch mount 应在 commissioning 阶段由主机镜像、配置管理或明确的主机 SOP 预置，`doctor/apply` 只检查并 loud-fail。

应用 release 不会顺便覆盖 `/opt/artifactflow/bin/afctl`。新版 bundle 自带的 `afctl` 用于执行该版 `plan/apply`；成功后再由 operator 显式安装到稳定路径。Ansible executor 保留为实验性路径，目前只接受 external PostgreSQL/Redis；完成真实多机物理验收前不属于 production-supported contract。

完整流程见 [`docs/operations/first-deployment.md`](../docs/operations/first-deployment.md)、
[`docs/operations/releases.md`](../docs/operations/releases.md) 与
[`docs/operations/maintenance.md`](../docs/operations/maintenance.md)。
