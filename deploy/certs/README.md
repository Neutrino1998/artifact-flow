# 静态 TLS 证书

`tls = "static"` 时，afctl 从稳定 control plane 挂载两个固定文件：

```text
/opt/artifactflow/control/certs/server.crt
/opt/artifactflow/control/certs/server.key
```

`server.crt` 必须包含 leaf + intermediates 完整链；客户端必须信任签发根 CA。私钥建议 `0600`。afctl 不生成自签名 fallback，缺少任一文件会在 doctor/apply loud-fail。

换证时原子覆盖两个文件，然后执行：

```bash
afctl --root /opt/artifactflow plan apply current
afctl --root /opt/artifactflow apply current
```

ACME site 不使用这里的证书。
