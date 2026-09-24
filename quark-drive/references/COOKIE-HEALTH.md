# Cookie 健康管理

> **目标**：cookie 不健康时快速恢复

---

## 30 天过期机制

夸克 cookie 30 天自动过期。过期后 WebDAV 服务**不报错**，但所有 API 请求都返回 401。

**特征**：
- `quark doctor` 显示 cookie 文件在，但 `quark login --check` 返非 0
- 列文件返回"成功"但目录为空
- 上传下载全 401

---

## 检测方式

### 方式 1 · `quark login --check`（推荐）
```bash
quark login --check
# 期望输出：✅ Cookie 仍然有效
# 失败输出：❌ 重新扫码
```

### 方式 2 · 直接打 API
```bash
curl -s --max-time 5 -G \
  -H "Cookie: $(cat ~/.config/quark-backup/cookie.txt)" \
  -o /dev/null -w "%{http_code}" \
  'https://drive-pc.quark.cn/1/clouddrive/file/sort' \
  --data-urlencode "pr=ucpro"
# 期望: 200
```

---

## 应急流程（3 步恢复）

### 步骤 1 · 重扫 QR
```bash
quark login
```
- 自动生成 QR + 上传 catbox 图床 + 发到锡哥 QQ
- 等锡哥「扫了」「确认登录」回复

### 步骤 2 · 验证 cookie 写回
```bash
ls -la ~/.config/quark-backup/cookie.txt
# 应该显示 mtime 在最近 1-2 分钟内
```

### 步骤 3 · 重启服务（关键）
```bash
quark stop && quark start
quark doctor
```
- 必须重启，因为服务启动时把 cookie 读进内存

---

## 三种救援方式（按优先级）

| 优先级 | 方式 | 命令 |
|:-:|:--|:--|
| 1 | 普通重扫码 | `quark login` |
| 2 | 备份恢复 | `cp /tmp/cookie-good.bak ~/.config/quark-backup/cookie.txt && quark stop && quark start` |
| 3 | 全新扫码 | 同方式 1（原理同上）|

---

## 不做的事（避免坑）

- ❌ **不删 cookie 文件单独重启** —— 服务假死
- ❌ **不写 cookie 健康监控 cron** —— 锡哥 17:00 拍板「不值得」（启动 3 秒手动）
- ❌ **不替 cookie 续期** —— 夸克不支持主动续期，只能重扫码

---

## 备份策略（建议）

| 频率 | 动作 |
|:--|:--|
| 每次重扫码成功 | 备份到 `/tmp/cookie-good.bak` |
| 每周一次 | 备份到 `/home/node/clawd_code/memory/backups/cookie-YYYY-MM-DD.txt` |

**自动化备份**（如有需要）：
```bash
# 加到 ~/.bashrc 或 wrapper 启动后
[ -f ~/.config/quark-backup/cookie.txt ] && cp ~/.config/quark-backup/cookie.txt /tmp/cookie-good.bak
```

---

_最后更新：2026-09-23 · 9-22 scope creep 教训后落档_
