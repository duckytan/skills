# WebDAV 配置详解

> **目标**：理解 quarkdrive-webdav 的原理，能手动调通

---

## 三件套

1. **quarkdrive-webdav** = Rust 二进制，把 WebDAV 协议转成夸克 API
2. **start-quark-webdav.sh** = 启动脚本（pkill 旧进程 + nohup 起 + PROPFIND 探活）
3. **rclone** = 通用命令行客户端（支持 WebDAV backend）

---

## 端口与凭据

| 项 | 值 |
|:--|:--|
| 监听 | `127.0.0.1:8080`（仅本机，非公网暴露）|
| 用户 | `admin` |
| 密码 | `admin`（本地用，可弱） |

**为什么弱密码**：服务仅监听 127.0.0.1，不暴露给公网。如果你要远程访问，**不要这么做** —— 应该用 SSH 反向隧道或 frp。

---

## rclone 集成

`~/.config/rclone/rclone.conf`：
```ini
[quark-webdav]
type = webdav
url = http://127.0.0.1:8080
vendor = other
user = admin
pass = <rclone-obscure 后的密码>
```

**注意**：`pass` 是 rclone obscure 后的密码，不是原始 `admin`。

---

## 常见操作

### 列文件（CLI）
```bash
curl -s --max-time 5 -u admin:admin -X PROPFIND -H "Depth: 1" \
  http://127.0.0.1:8080/
```

### 上传文件（rclone）
```bash
rclone copy /local/path quark-webdav:/remote/path --progress
```

### 下载文件
```bash
rclone copy quark-webdav:/remote/path /local/path --progress
```

### 同步（谨慎）
```bash
rclone sync /local/path quark-webdav:/remote/path --progress
# ⚠️ sync 会删对方不存在的本地文件，慎用
```

---

## 性能参考（9-22 实测）

| 操作 | 速度 | 备注 |
|:--|:--:|:--|
| 上传 | 13 KB/s | chenqimiao 上传实现简单，慢 |
| 下载 | 看夸克限速 | 一般 1-5 MB/s（看网络）|
| 列文件 | 即时 | 本地 PROPFIND |

**取舍**：上传慢是已知问题（基于 quarkdrive-webdav 上传层）。如果需要大文件上传，建议：
- 用官方客户端 + 转存
- 或换 kuake_cli（有分片上传 + 更好的 Token 管理）

---

## 服务生命周期

### 启动
```bash
quark start
# 或直调：
bash /home/node/tools/bin/start-quark-webdav.sh
```

### 停止
```bash
quark stop
# 或：
pkill -f quarkdrive-webdav
```

### 强制重启
```bash
quark stop && sleep 2 && quark start
```

---

## 日志位置

| 文件 | 内容 |
|:--|:--|
| `/tmp/quarkdrive-webdav.pid` | 进程 PID |
| `/tmp/quarkdrive-webdav.log` | 运行日志 |

---

## 故障排查

### 启动失败（PROPFIND 不返 207）
- **原因 1**：cookie 文件不存在 → `ls ~/.config/quark-backup/cookie.txt`
- **原因 2**：cookie 已过期 → `quark login --check`
- **原因 3**：端口冲突 → `lsof -i :8080`

### 服务起来了但列文件空
- **原因**：cookie 被删除但服务还在跑（内存里有 cookie 但新 cookie 丢了）
- **解决**：`quark stop && quark login && quark start`

### 上传成功但客户端看不到
- **原因**：rlcone 用缓存，等 30 秒或加 `--no-cache`

---

_最后更新：2026-09-23 · v3.1 方案阶段 3 落档_
