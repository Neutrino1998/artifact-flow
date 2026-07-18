# autoheal — 容器健康自愈

宿主 systemd timer 周期把 **unhealthy** 的容器 `docker restart` 拉起来,并留一条可审计
的重启痕迹供管理端面板代报。补齐自愈闭环的最后一环:

```
wedge → Caddy 被动摘出轮询(秒级)
     → 心跳 ts 停更、/admin/instances 面板变红(≤ 心跳 TTL,默认 300s)
     → 本 timer docker restart(≤ timer 间隔,默认 60s)
     → 容器重起、心跳恢复、进回轮询
```

deadman 的 faulthandler 栈仍走 `docker logs` 供事后定因;autoheal 只负责「把卡住的
拉起来」,不做归因。

## 组成

| 文件 | 作用 |
|---|---|
| `../scripts/autoheal.sh` | 扫 compose 容器,重启 unhealthy,追加 marker。可手动 `--dry-run` |
| `artifactflow-autoheal.service` | oneshot,跑一次脚本 |
| `artifactflow-autoheal.timer` | 每 60s 触发 service |
| `restart-marker.jsonl` | 运行时追加(gitignore):`{ts, instance_id, container, reason}` 每行一条 |

## 安装(真机)

```bash
# 使用当前已验收 release 的脚本，显式安装；afctl apply 不修改控制面工具
RELEASE=/opt/artifactflow/.artifactflow/releases/1.4.0
sudo install -m 0755 "$RELEASE/deploy/scripts/autoheal.sh" \
  /opt/artifactflow/bin/artifactflow-autoheal
sudo install -m 0644 "$RELEASE/deploy/autoheal/artifactflow-autoheal.service" \
  "$RELEASE/deploy/autoheal/artifactflow-autoheal.timer" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now artifactflow-autoheal.timer
systemctl list-timers artifactflow-autoheal.timer
/opt/artifactflow/bin/artifactflow-autoheal --dry-run    # 只报告不动手
```

## 两个必须知道的约束

- **与维护窗口互斥**:`afctl apply/maintenance` 会在 `control/maintenance/MAINTENANCE_ON`
  落旗标。autoheal 每轮先查旗标,在即整个 no-op 退出 ——
  不会把维护窗口里停掉的服务错误拉活。

- **归因经文件中转,脚本不碰 Redis**(保脚本十行级可审计):重启即向 `restart-marker.jsonl`
  追加一行;该目录 `:ro` 挂进 backend(`/app/autoheal`,见 compose),backend 心跳读本机
  `instance_id` 匹配的最近一条,经 `/admin/instances` 的 `last_autoheal` 在面板显示
  「autoheal 重启 ×N」。`docker restart` 保留容器身份(hostname/instance_id 不变),
  面板同一行连续、`started_at` 变新即重启轨迹。
  - 前提:backend 的 `instance_id` = 容器 hostname(docker 默认)。若显式设了
    `ARTIFACTFLOW_INSTANCE_ID` 覆盖,marker 里记的 hostname 会与心跳 id 不符、归因失配
    (默认部署不设,无此问题)。

## 真机验收(随内网发版窗口)

Mac 上无 systemd,只验过脚本语义(维护旗标互斥 / dry-run / marker 追加+截断 / backend
按 instance_id 过滤读取)与 marker→心跳→面板的代报链路(手写 marker 行验证)。**timer
周期触发 + `kill -STOP` 模拟 wedge → 面板红 → 自动重启 → 恢复绿** 的完整闭环留真机验。
