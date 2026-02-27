---
name: windows-notify
description: |
  Send Windows balloon notifications to system tray. Use when informing user of task completion,
  status updates, errors or warnings. Trigger phrases: "(send|show|display) notification",
  "(task|process) completed", "(operation|process|script) finished", "alert user", 
  "need to notify", "send alert", "(error|warning|notification) message".
---

# Windows 系统通知 (Windows Balloon Notifications)

零依赖的 Windows 原生通知系统，通过系统托盘向用户显示临时消息。支持自定义标题、内容、图标和持续时间。

## 💡 理念 (Philosophy)

创建符合 Windows UI 设计规范、用户体验优良的非侵入式通知系统。追求：
- **原生集成**: 完美融入 Windows 操作系统环境
- **轻量便携**: 无需外部依赖，零安装成本
- **高可控性**: 自定义参数丰富，满足多样化使用场景

## 🚀 使用场景 (When to Use)

使用此技能的时机：
- **脚本执行完成通知**: 后台任务结束时主动告知用户
- **关键状态更新**: 进度变更、完成、异常等提醒
- **错误/警告提示**: 无需立即交互的非严重错误
- **定时提醒**: 批处理、定时任务的结果通知

## 📋 技术规格 (Technical Specification)

| 属性 | 说明 | 备注 |
|------|------|------|
| **类型** | 气球通知 (Balloon Notification) | 突出但不中断工作流 |
| **载体** | Windows 系统托盘 (System Tray) | 原生 Windows 通知区域 |
| **实现** | PowerShell + .NET Framework | 跨 Windows 版本兼容 |
| **依赖** | 零外部依赖 | 使用系统内置组件 |
| **显示模式** | 自动消失 | 可自定义显示时间 (1-10秒建议) |

## 🎛️ 参数配置 (Parameters)

### 主要参数 (Primary Parameters)
| 参数 | 类型 | 默认值 | 限制 | 功能 |
|------|------|--------|------|------|
| `--title` | String | `"通知"` | 32 字符以内 | 通知标题栏显示 |
| `--message` | String | `"这是一条通知"` | 200 字符以内 | 通知主体内容 |
| `--icon` | Enum | `"info"` | `info\|warning\|error\|none` | 通知图标类型 |
| `--duration` | Integer | `5000` | 1000-10000 ms | 显示持续时间 |

### 图标语义 (Icon Semantics)
| 图标类型 | 语义场景 | 视觉效果 |
|----------|----------|----------|
| `info` (信息) | 一般通知、操作完成 | 蓝色ℹ️ 圆形信息图标 |
| `warning` (警告) | 潜在问题、注意事项 | 黄色⚠️ 三角警告图标 |
| `error` (错误) | 错误状态、阻塞性问题 | 红色❌ 圆形错误图标 |
| `none` (无图标) | 中性通知、静默提示 | 无图标仅文字 |

## 🛠️ 使用方法 (Usage)

### 完整语法 (Full Syntax)
```bash
# 推荐路径 (Recommended Path)
node .opencode/skills/windows-notify/scripts/index.js --title "<标题>" --message "<内容>" --icon <图标> --duration <时长(毫秒)>
```

### 实际示例 (Practical Examples)
```bash
# 1. 基础信息通知 (Basic Info Notification)
node .opencode/skills/windows-notify/scripts/index.js --title "完成" --message "导出完成！共处理 125 个文件" --icon info

# 2. 警告提醒 (Warning Alert)
node .opencode/skills/windows-notify/scripts/index.js --title "容量警告" --message "磁盘空间已低于 10% 阈值！" --icon warning

# 3. 错误提示 (Error Message)
node .opencode/skills/windows-notify/scripts/index.js --title "上传失败" --message "文件过大，请压缩后重试" --icon error

# 4. 静默提醒 (Silent Reminder)
node .opencode/skills/windows-notify/scripts/index.js --title "提醒" --message "请记得提交今天的周报" --icon none --duration 8000

# 5. 长时间显示 (Extended Display)
node .opencode/skills/windows-notify/scripts/index.js --title "配置更新" --message "新配置已应用，请重新启动应用以生效" --icon info --duration 7000
```

## ✅ 最佳实践 (Best Practices)

### 通知内容建议
- **标题控制在 10 字以内**: 简洁明了，避免截断
- **正文保持在 50 字以内**: 长内容会被截断，影响阅读
- **使用主动语态**: "已完成" 而不是 "已完成的任务"
- **传达关键信息**: 结果、状态或需要立即关注的事项

### 优雅降级策略 (Graceful Degradation)
- **系统托盘隐藏**: 检测托盘状态，如有必要可转为桌面弹窗
- **权限受限**: 没有通知权限时提供备选提示方式
- **进程超时**: 5秒上限，防止长时间挂起

## ⚠️ 限制与注意事项 (Considerations)

### 系统兼容性
| Windows 版本 | 兼容性 | 备注 |
|--------------|--------|------|
| **Windows 10** | ✅ 完全支持 | 所有功能可用 |
| **Windows 11** | ✅ 完全支持 | 现代通知中心兼容 |
| **Windows 8.1** | ✅ 基础支持 | 功能完整 |
| **Windows 7** | ✅ 基础支持 | 需 .NET Framework 4.0+ |
| **Linux/macOS** | ❌ 不支持 | 仅限 Windows 平台 |

### 显示限制
- **字符限制**: 标题最大 32 字节，内容最大 200 字节
- **停留时间**: 超过 10 秒会被 Windows 自动忽略
- **屏幕分辨率**: 适配 1080p 及以上主流分辨率

### 使用频率建议
- **频次控制**: 避免 1 分钟内发送超过 5 条
- **分组通知**: 相关事件合并为单条通知（例如处理结果汇总：25/30 完成）

## 🧪 验证脚本 (Validation)

```bash
# 快速测试所有图标
node .opencode/skills/windows-notify/scripts/index.js --title "info图标" --message "信息通知测试" --icon info
node .opencode/skills/windows-notify/scripts/index.js --title "warning图标" --message "警告通知测试" --icon warning
node .opencode/skills/windows-notify/scripts/index.js --title "error图标" --message "错误通知测试" --icon error
node .opencode/skills/windows-notify/scripts/index.js --title "none图标" --message "无图标通知测试" --icon none

# 时长测试
node .opencode/skills/windows-notify/scripts/index.js --title "时长测试" --message "这是一条显示8秒的通知" --duration 8000
```

## 🔧 调试指导 (Troubleshooting)

**常见问题**：
- `权限被拒绝` - 以管理员权限运行 PowerShell 执行脚本
- `通知不显示` - 检查系统托盘是否被隐藏或禁用
- `图标无效` - 确保图标参数为 info/warning/error/none 之一
- `中文乱码` - 当前实现自动转义特殊字符，如仍有问题请使用英文内容测试

**诊断步骤**：
1. 确认 Node.js 环境正常
2. 检查 PowerShell 执行策略 (`Get-ExecutionPolicy`)
3. 验证 .NET Framework 版本是否 >= 4.0