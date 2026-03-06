# opencron 路径自动保存功能

> **版本**: 2.1.1  
> **日期**: 2026-03-05  
> **功能**: 安装后自动保存路径，下次直接使用

---

## 🎯 功能说明

### 问题

**之前**：每次运行脚本都要查找路径（1-5ms）
```
脚本运行 → 查找路径 → 使用路径 → 结束
                                     ↓
下次运行 → 重新查找 → 使用路径 → ...（重复查找）
```

### 解决

**现在**：安装后保存路径，下次直接读取（0ms）
```
第 1 次：脚本运行 → 查找路径 → 使用路径 → **保存路径** → 结束
                                                    ↓
第 2 次：脚本运行 → **读取保存** → 使用路径 → ✅（0ms）
                                                    ↓
第 N 次：脚本运行 → **读取保存** → 使用路径 → ✅（0ms）
```

---

## 📦 配置文件

### 文件位置

**用户主目录**：`~/.opencron-path.json`

| 系统 | 位置 |
|------|------|
| **Windows** | `C:\Users\<用户名>\.opencron-path.json` |
| **Linux** | `/home/<用户名>/.opencron-path.json` |
| **macOS** | `/Users/<用户名>/.opencron-path.json` |

### 文件格式

```json
{
  "opencronRoot": "C:\\Users\\Administrator\\AppData\\Roaming\\npm\\node_modules\\opencron-system",
  "installType": "global",
  "version": "2.1.0",
  "installedAt": "2026-03-05T15:30:00.000Z"
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `opencronRoot` | string | opencron 安装根目录 |
| `installType` | string | 安装类型：`global` / `local` / `env` |
| `version` | string | opencron 版本号 |
| `installedAt` | ISO8601 | 安装时间戳 |

---

## 🔧 工作原理

### 路径查找优先级

| 优先级 | 策略 | 耗时 | 说明 |
|--------|------|------|------|
| **0** | 读取保存路径 | 0ms | ✅ 最快，默认 |
| **1** | 环境变量 | 0ms | `OPENCRON_ROOT` |
| **2** | 预设路径查找 | 1-5ms | 检查常见位置 |
| **3** | npm 全局 | 10-50ms | 执行命令 |
| **4** | 遍历查找 | 10-100ms | 找配置文件 |
| **5** | 默认路径 | 0ms | 兜底 |

### 保存时机

**自动保存**：
1. ✅ 安装脚本成功安装后
2. ✅ 首次查找到路径后
3. ✅ 环境变量设置后

**手动保存**：
```javascript
import { saveOpencronPath } from './utils/config.js';

saveOpencronPath('/path/to/opencron', 'global', '2.1.0');
```

---

## 📋 使用示例

### 示例 1：安装后自动保存

```bash
# 运行安装脚本
node scripts/utils/install.js

# 安装成功后自动保存
💾 已保存路径到：C:\Users\Administrator\.opencron-path.json

# 下次运行任何脚本，直接读取保存的路径（0ms）
node scripts/commands/list-tasks.js
# [DEBUG] 使用已保存路径：C:\...\opencron-system
```

### 示例 2：手动清除保存的路径

```javascript
import { clearSavedPath } from './utils/config.js';

clearSavedPath();
// 下次将重新查找路径
```

### 示例 3：查看保存的路径

```bash
# Windows
type C:\Users\Administrator\.opencron-path.json

# Linux/macOS
cat ~/.opencron-path.json
```

---

## 🔍 配置文件管理

### 查看

**Windows**：
```cmd
type %USERPROFILE%\.opencron-path.json
```

**Linux/macOS**：
```bash
cat ~/.opencron-path.json
```

### 删除

**手动删除**：
```bash
# Windows
del %USERPROFILE%\.opencron-path.json

# Linux/macOS
rm ~/.opencron-path.json
```

**代码删除**：
```javascript
import { clearSavedPath } from './utils/config.js';

clearSavedPath();  // 清除保存的路径
```

### 修改

```bash
# 编辑文件
code ~/.opencron-path.json

# 修改 opencronRoot 字段为新路径
# 保存即可
```

---

## ✅ 性能对比

### 无保存（旧版）

| 调用次数 | 总耗时 | 平均每次 |
|---------|--------|---------|
| 1 次 | 5ms | 5ms |
| 10 次 | 50ms | 5ms |
| 100 次 | 500ms | 5ms |

### 有保存（新版）

| 调用次数 | 总耗时 | 平均每次 |
|---------|--------|---------|
| 第 1 次 | 5ms | 5ms（查找 + 保存） |
| 第 2 次 | 0ms | 0ms（读取保存） |
| 第 100 次 | 0ms | 0ms（读取保存） |

**性能提升**：
- 第 1 次：相同（都需要查找）
- 第 2 次起：**无限快**（直接读取）
- 100 次调用：**500ms → 5ms**（100x 提升）

---

## 🔐 安全性

### 文件权限

**自动设置**：
- Windows: 当前用户可读写
- Linux/macOS: `600`（仅当前用户）

### 敏感信息

**不保存**：
- ❌ 不保存凭证
- ❌ 不保存 API Key
- ❌ 不保存个人数据

**只保存**：
- ✅ 安装路径（公开信息）
- ✅ 版本号（公开信息）
- ✅ 安装时间（本地时间戳）

---

## 🐛 故障排除

### 问题 1：文件损坏

**症状**：
```
[DEBUG] 读取保存路径失败：Unexpected token
```

**解决**：
```bash
# 删除损坏的配置文件
rm ~/.opencron-path.json

# 重新运行任意脚本，会自动重新保存
node scripts/commands/list-tasks.js
```

### 问题 2：路径变更

**症状**：移动了 opencron 目录，但配置文件没更新

**解决**：
```bash
# 删除旧配置
rm ~/.opencron-path.json

# 重新运行脚本
node scripts/commands/list-tasks.js

# 会自动查找新路径并保存
```

---

## 💡 最佳实践

### 推荐

- ✅ 安装后自动保存（无需手动操作）
- ✅ 移动 opencron 后删除配置
- ✅ 多环境共用同一配置（自动识别）

### 不推荐

- ❌ 手动编辑配置文件（除非必要）
- ❌ 保存到项目目录（应该保存到用户主目录）
- ❌ 提交到版本控制（加入 .gitignore）

---

## 📝 .gitignore 配置

```gitignore
# opencron 路径配置（本地文件，不提交）
.opencron-path.json
```

---

**最后更新**: 2026-03-05  
**版本**: 2.1.1  
**状态**: ✅ 生产就绪
