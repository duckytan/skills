# Windows 弹窗通知 (Windows Pop-up Notifications)

**注意**: 此 skill 原名为 `windows-notify`，现已更新为 `pop-notify`

这是一个零依赖的 Windows 通知系统，通过系统托盘气泡向用户发送临时消息。支持自定义标题、内容、图标和持续时间。

## 功能特性

- **原生集成**: 使用 Windows 系统托盘显示通知
- **零依赖**: 不需要任何外部安装，利用系统内置 .NET Framework
- **自定义丰富**: 支持自定义标题、内容、多种图标和显示时长
- **跨版本兼容**: 支持 Windows 7, 8.1, 10, 11

### 图标类型

| 图标 | 用途 | 示例 |
|------|------|-----|
| `info` (信息) | 一般通知、操作完成 | "导出完成！" |
| `warning` (警告) | 潜在问题、注意 | "磁盘空间不足" |
| `error` (错误) | 错误状态、阻塞性问题 | "上传失败" |
| `none` (无图标) | 中性通知、静默提醒 | "定时任务完成" |

## 安装说明

 将整个 `pop-notify` 文件夹复制到您的 Claude AI 的 Skills 目录中，例如：
- **Claude Code**: `~/.claude/skills/` 
- **OpenCode**: `~/.opencode/skills/`
- **自定义**: 根据您的 AI 工具配置目录

## 使用方法

### 命令行使用

```bash
# 基本语法
node pop-notify/scripts/index.js --title "<标题>" --message "<内容>" --icon <图标> --duration <时长(毫秒)>

# 示例用法
node pop-notify/scripts/index.js --title "完成" --message "导出已成功完成" --icon info

node pop-notify/scripts/index.js --title "警告" --message "系统资源占用过高" --icon warning

node pop-notify/scripts/index.js --title "错误" --message "连接失败，请检查网络" --icon error 

node pop-notify/scripts/index.js --title "提醒" --message "请完成今日任务清单" --icon none --duration 6000
```

### 参数说明

| 参数 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `--title` | String | "通知" | 通知标题（建议10字内） |
| `--message` | String | "这是一条通知" | 通知内容（建议50字内） |
| `--icon` | String | "info" | 通知图标（info, warning, error, none） |
| `--duration` | Integer | 5000 | 显示时长，单位毫秒（1000-10000） |

## 应用场景

这个技能特别适用于：
- **自动化脚本的通知反馈** - 例如文件同步、备份完成
- **长时间操作的状态监控** - 例如批处理、数据处理完成
- **日常提前提醒** - 例如工作汇报、会议提醒
- **后台进程状态更新** - 例如定时任务、健康检查结果

## 技术说明

- **实现技术**: Node.js + PowerShell + .NET Framework
- **系统要求**: Windows 7 及以上版本
- **.NET 依赖**: 需要 .NET Framework 4.0 或更高版本（Windows 10/11 预装）
- **安全性**: 使用系统内置 API，无需外部权限