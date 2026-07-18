# afctl 运维速查

所有命令默认在 install root 工作；推荐显式传 `--root /opt/artifactflow`。

```bash
afctl site init --preset intranet|public
afctl site migrate-v1 --preset intranet|public --sandbox-runtime runsc|runc
afctl site validate
afctl doctor
afctl prepare [--gvisor-package FILE]
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
│   ├── inventory.ini       # 仅 Ansible executor
│   ├── certs/
│   ├── maintenance/
│   └── autoheal/
└── .artifactflow/
    ├── releases/<id>/
    ├── hotfix-bundles/<id>/
    ├── mutation.lock
    └── state.json
```

`state.json` 是 active/previous 的唯一权威；没有 `current` symlink 或 `.fleet-state`。release 目录是完整 effective snapshot：app release 直接含 app/config/deploy，config release 继承 expected base 的 app/deploy 后再成为完整 snapshot。

`plan` 永远只读。所有 release-changing apply/rollback/config apply 都使用同一 kernel lock 和 reconcile executor。成功探活前不写 state；失败时尝试恢复上一个成功 release，恢复失败则明确报错并保留维护页。

`prepare` 是单机 Linux 的便利入口。Ansible executor 只验证远端稳定主机能力；runsc/runc 与 scratch mount 应在 commissioning 阶段由主机镜像或配置管理预置。

完整指南见 [`docs/deployment.md`](../docs/deployment.md)。
