# opencron - 定时任务管理器

使用自然语言管理 opencron 定时任务系统。添加/删除/修改/查询任务，启用/禁用任务，查看执行日志和状态。支持 PM2 后台运行调度器。

## 功能特性

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

## 安装说明

将整个 `opencron` 文件夹复制到您的 AI 助手的 Skills 目录中：

- **OpenCode**: `~/.opencode/skills/`
- **Claude Code**: `~/.claude/skills/`
- **自定义**: 根据您的 AI 工具配置目录

## 使用方法

### 自然语言使用（推荐）

直接对 AI 助手说：

```
每天早上 8 点提醒我写日报
每 5 分钟检查一次服务器状态
查看当前有哪些定时任务
删除健康检查任务
```

### 脚本调用（备用）

```bash
# 查看任务列表
node opencron/scripts/smart-interface.js 有哪些任务

# 添加任务
node opencron/scripts/smart-interface.js "每天早上 8 点提醒我写日报"

# 查看任务详情
node opencron/scripts/get-task.js --id backup-daily
```

## 使用示例

### 添加任务

```bash
# 每天早上 8 点写日报
node opencron/scripts/add-task.js --schedule "0 8 * * *" --name "写日报"

# 每 5 分钟检查服务器
node opencron/scripts/add-task.js --schedule "*/5 * * * *" --name "检查服务器"
```

### 查询任务

```bash
# 查看所有任务
node opencron/scripts/list-tasks.js

# 查看任务详情
node opencron/scripts/get-task.js --id backup-daily
```

### 管理任务

```bash
# 禁用任务
node opencron/scripts/disable-task.js --id cleanup-hourly

# 启用任务
node opencron/scripts/enable-task.js --id daily-report

# 删除任务
node opencron/scripts/remove-task.js --id old-task
```

## 配置文件

opencron 配置文件位于：`project/opencron/opencron-config.json`

### 任务配置格式

```json
{
  "id": "任务唯一 ID",
  "name": "任务名称",
  "script": "tasks/脚本文件名.js",
  "schedule": {
    "type": "interval|daily|weekly|once",
    "minutes": 数字,      // interval 类型需要
    "at": "HH:MM",        // daily/weekly 类型需要
    "day": "monday",      // weekly 类型需要
    "onceAt": "2026-03-05 10:00:00"  // once 类型需要
  },
  "enabled": true,
  "retryOnError": true,
  "maxRetries": 3
}
```

## 时间格式转换

| 用户说的话 | schedule 配置 |
|-----------|--------------|
| "每小时" | `{ type: "interval", minutes: 60 }` |
| "每 5 分钟" | `{ type: "interval", minutes: 5 }` |
| "每天早上 8 点" | `{ type: "daily", at: "08:00" }` |
| "每天下午 3 点半" | `{ type: "daily", at: "15:30" }` |
| "每周一上午 9 点" | `{ type: "weekly", day: "monday", at: "09:00" }` |
| "每周五下午 5 点" | `{ type: "weekly", day: "friday", at: "17:00" }` |
| "2026-03-05 10:00 执行" | `{ type: "once", onceAt: "2026-03-05 10:00:00" }` |

## 技术架构

### opencron 核心特性

- **调度引擎** - Node-Cron（稳定可靠的调度库）
- **配置管理** - JSON 配置文件 + Zod 验证
- **任务执行** - 子进程管理 + 重试机制
- **日志系统** - 简单文件日志
- **安全机制** - 脚本白名单验证
- **重试策略** - 指数退避 + 随机抖动

### PM2 集成

opencron 使用 PM2 作为进程管理器，支持：

- 后台运行，不占用终端
- 开机自启动
- 自动重启
- 日志管理

常用 PM2 命令：

```bash
# 查看状态
pm2 list opencron

# 重启
pm2 restart opencron --update-env

# 停止
pm2 stop opencron

# 查看日志
pm2 logs opencron --lines 20
```

## 应用场景

- **每日备份** - 每天凌晨自动备份重要数据
- **定时清理** - 每小时清理临时文件
- **健康检查** - 定期检查服务器状态
- **定时报告** - 每天/每周生成工作报告
- **提醒通知** - 定时提醒会议、任务等

## 文件结构

```
opencron/
├── SKILL.md               # AI 使用指南
├── README.md              # 用户文档
├── package.json           # 项目配置
├── skill.yml              # Skill 定义
├── scripts/               # 可执行脚本
│   ├── add-task.js        # 添加任务
│   ├── remove-task.js     # 删除任务
│   ├── update-task.js     # 更新任务
│   ├── list-tasks.js      # 查看任务
│   ├── get-task.js        # 查看任务详情
│   ├── enable-task.js     # 启用任务
│   ├── disable-task.js    # 禁用任务
│   ├── check-logs.js      # 查看日志
│   ├── check-status.js    # 检查状态
│   ├── check-env.js       # 检查环境
│   ├── doctor.js          # 健康检查
│   ├── init.js            # 初始化
│   ├── install-deps.js    # 安装依赖
│   ├── install.js         # 安装脚本
│   ├── smart-interface.js # 智能接口
│   ├── start-scheduler.js # 启动调度器
│   └── stop-scheduler.js  # 停止调度器
└── references/            # 参考文档
    ├── PATH-RESOLUTION.md
    ├── PATH-SAVING.md
    └── PATH-UPDATE-SUMMARY.md
```

## 注意事项

1. **配置文件格式**: 必须是有效的 JSON
2. **任务 ID**: 必须唯一，建议使用小写字母 + 横线
3. **脚本路径**: 相对于 `tasks/` 目录
4. **时间格式**: 24 小时制，如 "08:00" 表示早上 8 点
5. **星期格式**: 英文小写，如 "monday", "tuesday"
6. **任务状态**: 修改 `enabled` 字段来启用/禁用任务

## 依赖要求

- Node.js >= 14.0.0
- PM2（可选，用于后台运行）

## 相关文档

- [项目文档](project/opencron/README.md)
- [配置文件](project/opencron/opencron-config.json)
- [调度器源码](project/opencron/src/scheduler.js)

## 许可证

MIT License
