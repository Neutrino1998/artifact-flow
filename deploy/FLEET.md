# Fleet v1 已退役

Shell Fleet 状态机已由 `afctl` 取代。`deploy/scripts/fleet.sh` 仅保留少量旧命令到 `afctl` 的兼容转发，不再解析 topology、维护 release 状态或执行 SSH。

新命令、目录布局和 v1 迁移见 [`AFCTL.md`](AFCTL.md) 与
[`docs/operations/releases.md`](../docs/operations/releases.md)。不要为兼容桥增加新功能。
