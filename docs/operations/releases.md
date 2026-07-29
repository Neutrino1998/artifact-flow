# Release、升级与回滚

Release 是从一个干净 Git commit 构建出的不可变交付物。`afctl` 负责在目标站点物化并应用它；两者共同避免“某台机器上的源码目录恰好是什么状态”成为部署真相。

## 构建机准备

构建机需要：

- Go 1.23+；
- Docker daemon、Buildx 和 Compose v2；
- Python 3；
- Git、tar、gzip、sha256sum；
- 能拉取应用、Sandbox 和基础设施镜像的网络；
- 与目标一致的平台参数：`linux/amd64` 或 `linux/arm64`。

Release 脚本拒绝 staged、unstaged 和 untracked 文件。配置临时修改不应混进未提交 Release；生产热修有独立流程。

## 构建 Release

新站点首包应携带基础设施镜像：

```bash
./scripts/release.sh 1.4.0 --with-infra --platform linux/amd64
```

已加载对应 content-addressed infra 镜像的站点，日常应用更新可使用：

```bash
./scripts/release.sh 1.4.1 --app-only --platform linux/amd64
```

app-only bundle 不声明也不读取 Caddy、PostgreSQL 或 Redis 镜像；目标机物化
Release 时从当前已成功 Release 继承这三项精确引用。没有 current Release 的新
站点会拒绝 app-only，首包必须使用 `--with-infra`。若要升级任一基础设施镜像，
同样必须改用 `--with-infra`，使新镜像随 Release 一起进入离线包。

输出包括：

```text
dist/releases/1.4.1/
├── afctl
├── manifest.json
├── artifactflow-app-1.4.1.tar.gz
├── artifactflow-config-1.4.1.tar.gz
├── artifactflow-deploy-1.4.1.tar.gz
└── artifactflow-sandbox-1.4.1-amd64.tar.gz

dist/artifactflow-release-1.4.1-amd64.tar
dist/artifactflow-release-1.4.1-amd64.tar.sha256
```

外层 transport tar 不重复压缩内部镜像 tar，适合离线传输。一个 bundle 目录只允许一份固定 `manifest.json`；同一个 Release ID 不得复用为不同内容。

## 升级

先校验并解包 transport。升级时使用新 bundle 自带的 `afctl`：

```bash
sudo /media/1.4.1/afctl --root /opt/artifactflow plan apply /media/1.4.1
sudo /media/1.4.1/afctl --root /opt/artifactflow apply /media/1.4.1
sudo install -m 0755 /media/1.4.1/afctl /opt/artifactflow/bin/afctl
```

推荐发布记录至少保存：

- transport checksum；
- Release ID、platform 和源 commit；
- `plan` 输出；
- Apply 开始/结束时间；
- 数据库备份点；
- 验收结果和执行人。

Apply、rollback 和 config apply 共用一个 kernel advisory lock。不要并发执行多条 mutation 命令。

## 状态与不可变性

```text
/opt/artifactflow/.artifactflow/
├── releases/<id>/
├── hotfix-bundles/<id>/
├── mutation.lock
└── state.json
```

`state.json` 是 current/previous 的唯一权威。没有需要同步的 `current` symlink 或第二状态文件。Release 目录是完整 effective snapshot，不应作为可写 bind mount，也不应手工修改。

现场 `.env`、入站证书、出站 CA 信任锚、欢迎/品牌静态内容和维护状态始终在 `control/`，不会随 Release 切换；在线通知保存在数据库中。

## 回滚

```bash
sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow plan rollback
sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow rollback
```

Rollback 解析 `state.previous` 后复用同一个 Apply executor。它能恢复应用、配置、部署文件和精确镜像，但不能承诺自动逆转已经执行的数据库 migration。

因此涉及不向后兼容 schema/data 变更的版本，必须在发布计划中单独设计数据库恢复路径；不要把 `afctl rollback` 当成数据库时间机器。

## 配置热修

生产机无需 Git 或 Docker build。先从当前完整配置创建有基线绑定的 workspace：

```bash
sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow \
  config checkout /tmp/artifactflow-config-hotfix
```

修改其中的 `config/`，再应用：

```bash
sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow \
  config apply --id hotfix-model-20260719-153000 \
  /tmp/artifactflow-config-hotfix
```

`config apply` 会生成并保留 config bundle，继承当前 base 的 app、deploy 和 Sandbox，形成新的完整 Release，再走同一个 Apply。若 checkout 之后 current Release 已变化，它会失败并要求重新 checkout，不会悄悄重基线。

热修验证通过后，应把等价变更回写到源码配置并进入下一个正式应用 Release，避免长期只有生产现场知道该差异。

## 修改 `.env` 或证书

这两类属于站点状态，不需要 config hotfix：

```bash
sudo vi /opt/artifactflow/control/.env
sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow site validate
sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow plan apply current
sudo /opt/artifactflow/bin/afctl --root /opt/artifactflow apply current
```

静态证书轮换同样是在 `control/certs/` 原子替换证书和私钥后执行 `apply current`。
内网 HTTPS Tool/MCP 的信任锚放在 `control/trust/ca-certificates/*.crt`；增加、
替换或删除后同样执行 `apply current`，使所有 Backend 副本重建系统 CA bundle。

`POSTGRES_*` 只参与空 volume 的首次初始化。已有数据库改密码时，必须先在 PostgreSQL 内修改账号密码，再同步 `.env` 中的 `POSTGRES_PASSWORD` 和应用 URL。

## 从 Fleet v1 迁移

旧安装若还有 `/opt/artifactflow/deploy/.env`：

```bash
sudo ./1.4.0/afctl --root /opt/artifactflow site migrate-v1 \
  --preset intranet --sandbox-runtime runsc
```

该命令迁移目标机 `.env`、证书和站点内容，但不会导入旧的多套 Release 状态。随后执行 `doctor`、`plan`，再 Apply 一份完整 v2 bundle。

非常早期安装若 Docker volume 仍使用 `deploy_*` 前缀，第一次 v2 Apply 前按仓库的 [volume migration runbook](https://github.com/Neutrino1998/artifact-flow/blob/main/deploy/MIGRATION-project-name.md)在维护窗口迁移。`afctl` 不猜测、合并或删除旧生产 volume。
