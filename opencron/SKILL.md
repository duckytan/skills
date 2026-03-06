---
name: opencron
description: |
  管理 opencron 定时任务系统。使用自然语言添加、删除、查看定时任务。
  触发词：添加任务、创建定时任务、查看任务列表、删除任务、计划任务、定时任务。
  
  **核心原则**：
  1. 精简反馈 - 少说废话，多说重点
  2. Question 工具 - 二选一/多选时必须用按钮，不让用户打字
  3. 智能推荐 - AI 直接推荐最佳方案，用户确认即可
  4. 自然语言 - 用户说人话，AI 直接理解
  
  **安装指令**：
  ```bash
  # 默认：全局安装（推荐）
  npm install -g opencron-system
  
  # 备选：权限问题时的解决方案
  npm install opencron-system
  ```

version: 2.1.1
---

# opencron 任务管理器

## 📦 安装指南

### 默认方案：全局安装（推荐）

**适用于**：个人使用、系统级工具

```bash
# 安装命令
npm install -g opencron-system

# 验证安装
opencron --version

# 预期输出：opencron-system@2.1.0
```

**安装位置**：
- **Windows**: `C:\Users\<用户名>\AppData\Roaming\npm\node_modules\opencron-system`
- **Linux/macOS**: `/usr/local/lib/node_modules/opencron-system`
- **命令位置**: 全局可用（任何目录都能使用）

**优点**：
- ✅ 任何目录都能使用
- ✅ 系统级工具，一次安装到处使用
- ✅ 不需要在每个项目重复安装

**可能遇到的问题**：

#### 问题 1：权限被拒绝

```bash
npm ERR! Error: EACCES: permission denied
```

**解决方案**：

**Windows**：
```bash
# 以管理员身份运行 CMD/PowerShell
# 右键 → 以管理员身份运行
npm install -g opencron-system
```

**Linux/macOS**：
```bash
# 方案 A: 使用 sudo（不推荐，但可以解决）
sudo npm install -g opencron-system

# 方案 B: 修复 npm 权限（推荐）
mkdir ~/.npm-global
npm config set prefix '~/.npm-global'
echo 'export PATH=~/.npm-global/bin:$PATH' >> ~/.bashrc
source ~/.bashrc
npm install -g opencron-system
```

---

### 备选方案：局部安装（权限问题时的最后手段）

**适用于**：AI 工作空间、项目依赖、无法全局安装

```bash
# 进入项目目录
cd D:\AI-Project\AI-AgentWorkSpace-Ducky

# 局部安装
npm install opencron-system

# 验证安装
npx opencron --version
```

**安装位置**：
- **项目内**: `D:\AI-Project\AI-AgentWorkSpace-Ducky\node_modules\opencron-system`
- **命令位置**: 仅在项目目录内可用

**使用方式**：
```bash
# 在项目目录内
cd D:\AI-Project\AI-AgentWorkSpace-Ducky
npx opencron --version  # ✅ 可用

# 在其他目录
cd D:\OtherProject
opencron --version  # ❌ 不可用
```

**优点**：
- ✅ 不需要管理员权限
- ✅ 项目依赖，版本锁定
- ✅ 团队协作时版本一致

**缺点**：
- ❌ 只能在项目目录内使用
- ❌ 每个项目需要单独安装

---

## 🎯 什么时候使用

当用户需要管理定时任务时，**不要**让他们学习命令，**直接用自然语言理解**：

- ✅ "每天早上 8 点提醒我写日报" → 添加任务
- ✅ "看看有哪些定时任务" → 查看任务列表
- ✅ "删除健康检查任务" → 删除任务
- ✅ "每 5 分钟检查一次服务器" → 添加任务
- ✅ "任务执行了吗？" → 查看日志

---

## ✨ 核心优势

### ❌ 以前的体验

```
用户：每天早上 8 点提醒我写日报
Skill: ❌ 请使用 /crontab add-task --id xxx --schedule "0 8 * * *"
```

### ✅ 现在的体验

```
用户：每天早上 8 点提醒我写日报
AI: ✅ 好的，已添加每天早上 8 点的日报提醒
   - 任务名称：写日报
   - 执行时间：每天 08:00
   - 下次执行：明天 08:00
```

---

## 🎯 功能列表

### 核心功能（增删改查）

1. **添加任务** - 创建新的定时任务
2. **删除任务** - 移除已有任务
3. **修改任务** - 更新任务配置
4. **查询任务** - 查看任务列表和详情
5. **启用/禁用** - 控制任务执行状态
6. **日志查看** - 查看任务执行日志

### 辅助功能

7. **状态检查** - 查看调度器运行状态
8. **配置验证** - 检查配置文件格式
9. **健康检查** - 执行健康检查
10. **调度器管理** - 启动/停止调度器

---

## 🔧 AI 内部处理流程

当用户说"每天早上 8 点提醒我写日报"时：

**AI 自动完成**：

1. ✅ **理解意图** → 用户想添加任务
2. ✅ **提取时间** → "每天早上 8 点" = `daily at 08:00`
3. ✅ **提取任务** → "写日报" = 任务描述
4. ✅ **生成配置** → 创建 JSON 配置
5. ✅ **生成脚本** → 创建任务脚本文件
6. ✅ **验证配置** → 检查 cron 表达式
7. ✅ **重启调度器** → PM2 重启
8. ✅ **反馈结果** → 告诉用户"搞定！"

**用户完全不需要知道**：
- ❌ cron 表达式是什么
- ❌ 配置文件在哪里
- ❌ PM2 是什么
- ❌ 任务 ID 怎么生成

---

## 🚀 使用方式

### 方式 1：AI 智能处理（推荐）

**AI 完整交互流程**：

```
1. 用户说："每天早上 8 点提醒我写日报"

2. AI 调用脚本：
   node smart-interface.js "每天早上 8 点提醒我写日报"

3. 脚本返回结果（精简版）：
   ✅ 已设置完成！
   📋 任务信息：...
   🔔 通知设置：...

4. AI 需要确认时 → 使用 question 工具：
   "需要修改通知设置吗？"
   【按钮：修改设置】 【按钮：不用改】

5. 用户点击按钮后：
   - 点"修改" → AI 帮助修改配置
   - 点"不用改" → 任务完成
```

**完整示例**：

```
用户：每天早上 8 点提醒我写日报

AI: ✅ 已设置完成！

📋 任务信息：
   - 名称：写日报
   - 执行时间：每天 08:00
   - 下次执行：03-05 08:00
   
🔔 通知设置：
   - ⏰ 启动：✅ 准时提醒，完成后不打扰
   - ✅ 完成：❌
   - ❌ 失败：✅（建议保持开启）

📝 已创建任务脚本

需要修改通知设置吗？
```
↓ **AI 调用 question 工具**

```
【按钮：修改设置】  【按钮：不用改】
```

**用户点击"不用改"**

```
AI: ✅ 好的，任务已就绪！
   
   下次执行：明天 08:00
   PM2 状态：🟢 运行中
```

---

### Question 工具使用规则（强制）

**AI 必须在以下场景使用 question 工具**：

| 场景 | 示例 | Question 工具 |
|------|------|--------------|
| **二选一** | "开启还是关闭？" | 【按钮：开启】 【按钮：关闭】 |
| **多选一** | "选哪种通知方式？" | 【按钮 A】 【按钮 B】 【按钮 C】 |
| **确认操作** | "确认删除吗？" | 【按钮：确认删除】 【按钮：取消】 |
| **修改确认** | "需要修改通知设置吗？" | 【按钮：修改设置】 【按钮：不用改】 |
| **任务类型** | "哪种任务？" | 【按钮：监控类】 【按钮：提醒类】 【按钮：备份类】 |

**禁止行为**：
- ❌ 让用户打字回复"是/否"
- ❌ 让用户打字回复选项
- ❌ 长篇大论解释

**正确做法**：
- ✅ 提供现成按钮
- ✅ 一句话说清楚
- ✅ AI 直接推荐最佳方案
- ✅ 脚本输出后，AI 补充 question 工具

---

### 方式 2：调用 skill 脚本（备用）

```bash
# 查看任务列表
node .opencode/skills/opencron/scripts/smart-interface.js 有哪些任务

# 添加任务（AI 自动调用）
node .opencode/skills/opencron/scripts/smart-interface.js "每天早上 8 点提醒我写日报"
```

**注意**：脚本只负责执行和返回结果，**不负责用户交互**。AI 需要根据脚本输出，适时调用 question 工具。

## 📋 配置文件位置

```
D:\AI-Project\AI-AgentWorkSpace-Ducky\project\opencron\opencron-config.json
```

### 配置结构

```json
{
  "version": "2.0",
  "timezone": "Asia/Shanghai",
  "tasks": [
    {
      "id": "任务唯一 ID",
      "name": "任务名称",
      "script": "tasks/脚本文件名.js",
      "schedule": {
        "type": "interval|daily|weekly|once",
        "minutes": 数字，      // interval 类型需要
        "at": "HH:MM",        // daily/weekly 类型需要
        "day": "monday",      // weekly 类型需要
        "onceAt": "2026-03-05 10:00:00"  // once 类型需要
      },
      "enabled": true,
      "retryOnError": true,
      "maxRetries": 3
    }
  ]
}
```

## 🕐 时间格式转换

| 用户说的话 | schedule 配置 |
|-----------|--------------|
| "每小时" | `{ type: "interval", minutes: 60 }` |
| "每 5 分钟" | `{ type: "interval", minutes: 5 }` |
| "每天早上 8 点" | `{ type: "daily", at: "08:00" }` |
| "每天下午 3 点半" | `{ type: "daily", at: "15:30" }` |
| "每周一上午 9 点" | `{ type: "weekly", day: "monday", at: "09:00" }` |
| "每周五下午 5 点" | `{ type: "weekly", day: "friday", at: "17:00" }` |
| "2026-03-05 10:00 执行" | `{ type: "once", onceAt: "2026-03-05 10:00:00" }` |

## ⚠️ 注意事项（2026-03-05 审查更新）

### 已知问题

1. **时间解析** → 中文编码问题，命令行测试可能失败，但 AI 环境正常
2. **通知功能** → 配置已设计，scheduler.js 集成中（待实施）
3. **脚本目录** → 计划重构为 commands/ + tools/ + utils/（待实施）
4. **Question 工具** → AI 层面调用，不是脚本功能（已明确）

### 实施建议

**AI 使用时**：
1. 调用脚本 → 获取结果
2. 显示脚本输出
3. **需要确认时调用 question 工具**
4. 根据用户选择执行后续操作

**时间解析失败时**：
```
AI: ❌ 没找到时间信息

💡 试试这样说：
   - "每天早上 8 点提醒我"
   - "每 5 分钟检查一次"
   - "每周一上午 9 点开会"
```

**PM2 未运行时**：
```bash
PM2 未运行，先启动：pm2 start .pm2/ecosystem.config.json
```

## 🔧 常用 PM2 命令

```bash
# 查看状态
pm2 list opencron

# 重启
pm2 restart opencron --update-env

# 停止
pm2 stop opencron

# 查看日志
pm2 logs opencron --lines 20

# 查看实时日志
pm2 logs opencron --lines 20 --timestamp
```

## ✅ 最佳实践

1. **自然语言优先** - 别让用户学习 cron 表达式
2. **即时反馈** - 添加后立即告诉用户下次执行时间
3. **容错处理** - 时间解析失败时主动询问
4. **上下文理解** - 记住刚才添加的任务，支持多轮对话

## 🧪 验证示例

```
用户：帮我添加一个每天早上 8 点提醒我写日报的任务

AI: ✅ 已设置完成！

📋 任务信息：
   - 名称：写日报
   - 执行时间：每天 08:00
   - 下次执行：03-05 08:00
   
🔔 通知设置：
   - ⏰ 启动：✅ 准时提醒，完成后不打扰
   - ✅ 完成：❌
   - ❌ 失败：✅（建议保持开启）

📝 已创建任务脚本

需要修改通知设置吗？
```
↓ **AI 调用 question 工具**

```
【按钮：修改设置】  【按钮：不用改】
```

**用户点击"不用改"**

```
AI: ✅ 好的，任务已就绪！
   
   下次执行：明天 08:00
   PM2 状态：🟢 运行中
```

---

## 🚧 待实施功能（2026-03-05 审查结果）

### P0 - 必须完成

- [ ] **通知功能集成** - 在 scheduler.js 中实现 onStart/onSuccess/onError 逻辑
- [ ] **中文编码优化** - 改善本地测试体验

### P1 - 应该完成

- [ ] **脚本目录重构** - 分类为 commands/ + tools/ + utils/
- [ ] **错误处理改进** - 提供更好建议
- [ ] **配置同步** - 确保 example 和实际一致

### P2 - 可以完成

- [ ] **Debug 模式** - 减少正常日志
- [ ] **类型定义** - JSDoc
- [ ] **README 更新** - 反映新功能

---

**最后更新**: 2026-03-05  
**版本**: 2.1.1  
**审查状态**: ✅ 已审查
