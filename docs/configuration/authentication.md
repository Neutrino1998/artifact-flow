# 企业认证配置

ArtifactFlow 可以在本地密码登录之外启用一个远端 bearer userinfo 登录来源。它不是
OAuth2/OIDC 客户端：浏览器从企业门户回跳得到一次上游 token，Backend 只用该 token
读取一次用户信息，随后签发与本地登录相同的 8 小时 ArtifactFlow JWT。上游 token
不会写入数据库、Cookie、日志或业务 API。

## 配置位置与生效方式

仓库中的 `config/auth/remote_bearer_userinfo.yaml` 默认关闭。生产配置写入目标机：

```text
/opt/artifactflow/control/auth/remote_bearer_userinfo.yaml
```

`afctl apply current` 会把 `control/auth/` 只读挂载到 release 和所有 Backend 的
`config/auth/`。Backend 只在启动时读取文件；配置没有 DB 副本和热更新，修改后必须
重建全部 Backend。目录或文件不存在等同于 `enabled: false`。

## 示例

```yaml
version: 1
enabled: true

provider:
  id: enterprise_sso
  display_name: 企业统一认证
  type: remote_bearer_userinfo

login:
  url: https://sso.example.internal/login
  callback_url: https://artifactflow.example.internal/auth/sso/callback
  return_param: entryPath
  token_param: authorization_key
  state_ttl_seconds: 300

userinfo:
  url: https://sso.example.internal/auth/info
  connect_timeout_seconds: 5
  read_timeout_seconds: 10
  allow_insecure_http: false
  department_separator: "/"
  fields:
    subject: user.id
    username: user.username
    display_name: user.name
    enabled: user.enabled
    department_path: user.superiorDeptName
    department_leaf: user.dept.name
```

`provider.id` 是认证来源的稳定命名空间。同一个身份系统即使换地址也应保留该值；接入
另一个身份系统时使用新值。用户身份由 `(provider.id, subject)` 唯一定位，不按可重复
的展示 username 合并或认领本地密码用户。

字段路径只支持对象上的点路径，不支持数组、JMESPath、脚本或表达式。`subject` 必须是
稳定且不会回收的字符串或整数；`username` 必须满足 ArtifactFlow 的用户名规则；
`enabled` 必须是 JSON boolean。

## HTTP、HTTPS 与部门语义

登录、callback 和 userinfo 都支持绝对 HTTP/HTTPS URL。HTTPS 始终校验证书，不能通过
配置关闭；私有 CA 放入 `control/trust/ca-certificates/`。任一 URL 使用 HTTP 时，必须
显式设置 `userinfo.allow_insecure_http: true`，用于确认 bearer 或登录跳转会经过明文
链路；生产应优先使用 HTTPS。

部门路径按 `department_separator` 拆分，路径末级必须与 `department_leaf` 相同。路径和
末级同时缺失或为空表示“无部门”，是合法的最小权限状态；用户以前有部门时，下次登录
明确返回无部门会清空旧归属。只有一边缺失、空层级或末级不一致会拒绝 exchange。

新远端用户默认是普通 `user`，上游角色和管理员标记不会授予本地权限。管理员可以在
ArtifactFlow 中禁用该用户；后续 SSO 不会自动重新启用。远端用户没有本地密码，也不能
使用自助改密或管理员密码重置。

## 公开握手接口

`GET /api/v1/auth/config` 是匿名只读接口，仅返回密码入口是否可用、SSO 是否启用、展示名
和 callback 需要读取的 token 参数名，不返回 userinfo 地址或字段映射。SSO start 会签发
短期 state，并用 HttpOnly Cookie 绑定当前浏览器；exchange 对 state 原子消费一次。配置
Redis 时 state 跨 Backend 共享；未配置 Redis 时仅使用适合单进程开发的 InMemory 实现。
