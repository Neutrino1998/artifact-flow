#!/usr/bin/env bash
# autoheal — 重启 unhealthy 的舰队容器,并留可审计的重启痕迹。
#
# Phase C 决策 5 的自愈闭环最后一环:wedge → Caddy 被动摘出轮询(秒级)→ 心跳过期
# 面板变红(≤TTL)→ 本脚本 `docker restart`(≤timer 间隔)→ 恢复进轮询。deadman 的
# faulthandler 栈仍走 docker logs 供事后定因;本脚本只负责「把卡住的拉起来」。
#
# 由宿主 systemd timer 周期触发(见 deploy/autoheal/*.timer),也可手动跑。
#
# Usage:
#   autoheal.sh              # 扫一轮,重启 unhealthy 容器
#   autoheal.sh --dry-run    # 只报告会重启谁,不动手
#
# Env(均可选):
#   AF_COMPOSE_FILE     compose 文件(默认 deploy/docker-compose.intranet.yml)
#   AUTOHEAL_SERVICES   监视的服务名,空格分隔(默认 "backend frontend caddy")
#   AUTOHEAL_MARKER     marker 文件路径(默认 <deploy>/autoheal/restart-marker.jsonl)
#   AUTOHEAL_MAX_LINES  marker 保留行数上限(默认 500,追加后按行数截断)
#
# 与维护窗口互斥:pause.sh 会主动停掉 backend/frontend 并落 MAINTENANCE_ON 旗标。
# 那些容器此刻「不在运行」是**有意**的,绝不能被 autoheal 拉活 —— 见 MAINTENANCE 旗标
# 检查:旗标在即整脚本 no-op 退出。
#
# 归因链(不直连 Redis,保脚本十行级可审计):重启即向 marker 追加一行
# {ts, instance_id, container, reason};该目录只读挂进 backend,实例心跳读本机
# instance_id 匹配的最近一条,经 /admin/instances 的 last_autoheal 字段在面板代报。
# `docker restart` 保留容器身份(hostname 不变 → instance_id 不变 → 面板同一行连续,
# started_at 变新即重启轨迹)。
#
# 退出码:0 = 正常(含「无 unhealthy」「维护窗口跳过」);非 0 = 脚本自身错误。
#
# 注:instance_id 取容器 .Config.Hostname(docker 默认 = 短容器 ID = backend 的
# socket.gethostname())。若部署显式设了 ARTIFACTFLOW_INSTANCE_ID 覆盖 hostname,
# 心跳上报的 id 会与此不一致、marker 归因需相应调整(默认部署不设,无此问题)。

set -uo pipefail  # 不用 -e:要遍历所有容器逐个处理,单个失败不该中断整轮

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

COMPOSE_FILE="${AF_COMPOSE_FILE:-$DEPLOY_DIR/docker-compose.intranet.yml}"
SERVICES="${AUTOHEAL_SERVICES:-backend frontend caddy}"
MARKER="${AUTOHEAL_MARKER:-$DEPLOY_DIR/autoheal/restart-marker.jsonl}"
MAX_LINES="${AUTOHEAL_MAX_LINES:-500}"
MAINT_FLAG="$DEPLOY_DIR/maintenance/MAINTENANCE_ON"

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

# ── 维护窗口互斥:旗标在 = pause.sh 有意停服,直接退出 ──
if [[ -f "$MAINT_FLAG" ]]; then
  echo "○ 维护窗口开启中(MAINTENANCE_ON),autoheal 跳过本轮"
  exit 0
fi

# ── compose CLI:V2 plugin 优先,V1 standalone 兜底(老 CentOS) ──
if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  echo "Error: 'docker compose' / 'docker-compose' 均不可用" >&2
  exit 1
fi

# ── 追加 marker 一行 + 按行数截断 ──
append_marker() {
  local container="$1" instance_id="$2" reason="$3" ts
  ts="$(date -u +%Y-%m-%dT%H:%M:%S)"  # naive UTC ISO,对齐后端 datetime.fromisoformat
  mkdir -p "$(dirname "$MARKER")" 2>/dev/null || true
  printf '{"ts":"%s","instance_id":"%s","container":"%s","reason":"%s"}\n' \
    "$ts" "$instance_id" "$container" "$reason" >> "$MARKER"
  # 追加型按行数截断(留最近 MAX_LINES 行)
  if [[ -f "$MARKER" ]]; then
    local n; n="$(wc -l < "$MARKER" 2>/dev/null || echo 0)"
    if (( n > MAX_LINES )); then
      tail -n "$MAX_LINES" "$MARKER" > "$MARKER.tmp" && mv "$MARKER.tmp" "$MARKER"
    fi
  fi
}

restarted=0
checked=0
for svc in $SERVICES; do
  # 一个服务在 --scale 下有多个容器,逐个查健康
  cids="$("${DC[@]}" -f "$COMPOSE_FILE" ps -q "$svc" 2>/dev/null || true)"
  [[ -z "$cids" ]] && continue
  while IFS= read -r cid; do
    [[ -z "$cid" ]] && continue
    checked=$((checked + 1))
    # 没有 healthcheck 的容器 .Health 为空 → 视作 "none",不动它
    health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null || echo unknown)"
    name="$(docker inspect -f '{{.Name}}' "$cid" 2>/dev/null | sed 's#^/##')"
    # fallback 用短 id(前 12 位):compose ps -q 给的是 64 位全 id,而 backend 的
    # INSTANCE_ID=docker 默认 hostname=短 id,直接用全 id 会对不上、marker 归因被丢。
    hostname="$(docker inspect -f '{{.Config.Hostname}}' "$cid" 2>/dev/null || echo "${cid:0:12}")"
    if [[ "$health" == "unhealthy" ]]; then
      if (( DRY_RUN )); then
        echo "would restart: $name ($svc, unhealthy, id=$hostname)"
      else
        echo "→ 重启 unhealthy 容器:$name ($svc, id=$hostname)"
        if docker restart "$cid" >/dev/null 2>&1; then
          append_marker "$name" "$hostname" "unhealthy"
          restarted=$((restarted + 1))
        else
          echo "  ✗ docker restart 失败:$name" >&2
        fi
      fi
    fi
  done <<< "$cids"
done

if (( DRY_RUN )); then
  echo "dry-run 完成:检查 $checked 个容器"
else
  echo "✓ autoheal 完成:检查 $checked 个,重启 $restarted 个"
fi
exit 0
