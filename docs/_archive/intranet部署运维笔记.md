# intranet 分支部署运维笔记

> 在内网部署 ArtifactFlow（CentOS 7 + Docker 19.03 + Mac arm64 dev → x86_64 服务器）时撞到的环境特定问题。
> 不是 ArtifactFlow 本身的 bug——下次部署到类似老环境会再遇到，所以按"症状 + 根因 + 修法"留个底。
> 跨部署都关心的功能 / 工程问题见同目录 `ArtifactFlow已知问题清单.md`。

## 容器运行时

### CentOS 7 + Docker 19.03 默认 seccomp profile 阻 `rseq` syscall

- **症状**：backend 容器启动后立刻挂，`docker logs` 看到 `RuntimeError: can't start new thread`，traceback 指向 asyncpg 连 postgres 时 asyncio 启动 DNS 解析的 executor 线程。
- **根因**：Debian Trixie（`python:3.11-slim` 现在的基础镜像）glibc ≥ 2.35 在 `pthread_create` 内部调 `rseq`（restartable sequences，性能优化）。Docker 19.03 ship 的 seccomp 默认 profile 不认这个 syscall（rseq 进入主线 Linux 是 4.18，Docker seccomp 跟进比较晚），EPERM 一发就让线程创建失败。CPython 把 EPERM 包成 `RuntimeError: can't start new thread`。
- **workaround**：`deploy/docker-compose.intranet.yml` 里给 backend 加 `security_opt: [seccomp:unconfined]`（见 commit `c22323f`）。受信内网环境可接受（容器还有 user/network/cgroup 隔离），不像 `--privileged` 那么放飞。
- **正经修法**：升 Docker ≥ 20.10（默认 seccomp 已经认 rseq）。但 CentOS 7 升 Docker 涉及 yum 仓库（内网拉不到）+ 服务停机，不是当下最优解。
- **如何识别误诊**：traceback 里看起来是 asyncpg / 数据库连接问题，但实际错的不是网络也不是 PG——asyncio 启动 thread pool 时就挂了。换 PG host / 检查 `.env` 都没用。看 traceback **最底下一层**有没有 `RuntimeError: can't start new thread` 是关键。

### `sed -i` 改 bind-mount 进容器的单文件，容器里看不到改动

- **症状**：host 上 `sed -i 's/.../.../' nginx.conf` 改了文件，host 上 `grep` 能看到新内容；容器里 `cat /etc/nginx/conf.d/default.conf` 看到的还是旧内容；nginx reload 不生效。
- **根因**：GNU sed 的 `-i` 不是真"原地编辑"——它写一个 temp 文件，然后 `rename(2)` 覆盖原文件。rename 之后路径指向**新 inode**，旧 inode 还在但没了路径。Docker 单文件 bind mount 在容器创建时按 inode 解析，绑的是旧 inode；rename 之后容器看的还是旧 inode（旧内容）。
- **修法 A（保 inode 编辑）**：`sed '...' nginx.conf > /tmp/new && cat /tmp/new > nginx.conf`。`> file` 是 `O_TRUNC` 重写同一 inode，docker 立刻能看到新内容，连 nginx reload 都不用。
- **修法 B（重建容器）**：`docker-compose ... up -d --force-recreate <service>`。强制重建容器会重新解析 bind mount，吃到新 inode。代价是服务有几秒中断。
- **避坑**：vim 默认行为也会 rename 写入（`backupcopy=auto`）。用 `:set backupcopy=yes` 强制原地写可以避免。

## 镜像构建 / 传输

### macOS arm64 → Linux amd64 必须用 `docker buildx`

- **症状**：从 Mac dev 机 `docker build` 出来的镜像，scp + `docker load` 到 x86_64 服务器后，`docker run` 立刻 `exec format error` 退出。
- **根因**：Apple Silicon 默认构建本机架构（arm64）。
- **修法**：用 `docker buildx build --platform linux/amd64 -t <image> -f Dockerfile . --load`。首次跑会自动启 QEMU 模拟，下载 `moby/buildkit:buildx-stable-1` builder image（~50s 一次性开销）。后续 build 都通过这个 builder 跑，速度比原生 build 慢 2-3x（Python 装 deps 那种 CPU 密集场景），但够用。
- **见**：`ArtifactFlow已知问题清单.md` 里记了 `scripts/release.sh` 要升级到默认走 buildx。

### `docker save` 输出不压缩，`gzip` 后能省 60%+

- **症状**：第一次 `docker save -o bundle.tar` 出来 1.1G，传输慢。
- **修法**：`docker save | gzip -c > bundle.tar.gz` 或者 `gzip bundle.tar`。我们 5 个镜像（backend 640MB / frontend 178MB / postgres 276MB / nginx 48MB / redis 41MB）gzip 后从 1.1G 降到 369MB，降 66%。
- **下游行为**：现代 Docker 的 `docker load -i bundle.tar.gz` 自动识别 gzip，不用先 `gunzip`。
- **`scripts/release.sh` 已经这么做**——`docker save "${IMAGES[@]}" | gzip > "$ARCHIVE"`，是我第一次手工打包没用脚本所以踩坑。

### macOS 打的 tar 在 Linux 解压有 `libarchive.xattr.com.apple.provenance` warning

- **症状**：`tar -xf foo.tar` 在 Linux 输出 `tar: Ignoring unknown extended header keyword 'libarchive.xattr.com.apple.provenance'`。
- **根因**：macOS Sequoia 给二进制文件加的来源追踪 xattr，写进 tar 的 PAX 扩展头。GNU tar 不认这个 keyword，跳过并 warn。
- **影响**：无。文件内容完整出来了，只是 Linux 用不到那段元数据。直接忽略 warning。

## CentOS 7 工具链

### 只有 `docker-compose`（v1 命令），没有 `docker compose`（v2 plugin）

- **症状**：`docker compose version` 报 `'compose' is not a docker command`。
- **修法**：用 `docker-compose` 命令（带横线）替代所有 `docker compose` 调用。我们的 `deploy/docker-compose.intranet.yml` v1/v2 都解析。
- **奇观**：这台机器上 `docker-compose --version` 输出 `Docker Compose version v5.1.3`——社区主线只有 v1.x 和 v2.x，v5 不知道哪儿来的 fork。**实测能解析我们的 yaml**（profiles / `depends_on.condition: service_healthy` / `${VAR:?}` 都认），不深究。

### 80 端口被业务进程占用 → `AF_HTTP_PORT=8080`

- **症状**：`docker-compose up -d` 起 nginx 时 `Bind for 0.0.0.0:80 failed: port is already allocated`。本次部署是被一个 `streamlit` 进程占着 80。
- **修法**：`.env` 里设 `AF_HTTP_PORT=8080`（或其他空端口），`docker-compose.intranet.yml` 里 nginx 写的就是 `${AF_HTTP_PORT:-80}:80`，无需改 yaml。
- **`.env.intranet.example` 已加了这条注释提示**（见 commit `d71218a`）。

## 部署完后的验证

### 健康检查只覆盖 backend / postgres / redis，nginx / frontend 默认显示 `Up` 不带 `(healthy)`

- **现象**：`docker-compose ps` 看到 nginx/frontend 只标 `Up`，没有 `(healthy)`。
- **解释**：我们 compose 文件没给 nginx 和 frontend 写 `healthcheck:` 字段（nginx 配置静态、frontend Next.js standalone 启动后基本无状态），不是 bug。
- **真实验证**：`curl http://localhost:8080/` 看 frontend、`curl http://localhost:8080/health/ready` 看后端经 nginx 路由——两条都返回 200 就说明 nginx → backend / nginx → frontend 链路通。
