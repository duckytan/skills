#!/usr/bin/env node
/**
 * opencron 智能任务接口 v2.0
 * 
 * 核心改进：
 * 1. 精简反馈 - 少说废话，多说重点
 * 2. Question 工具 - 用按钮代替输入
 * 3. 智能推荐 - AI 直接推荐最佳方案
 */

import { execSync } from 'child_process';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { getOpencronRoot, findConfigFile, validateOpencronRoot } from '../utils/path-resolver.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// 智能查找 opencron 根目录
const OPENCRON_ROOT = getOpencronRoot();
const CONFIG_FILE = findConfigFile(OPENCRON_ROOT);

// 验证路径
const validation = validateOpencronRoot(OPENCRON_ROOT);
if (!validation.valid) {
  console.error('❌ opencron 路径验证失败');
  console.error(`\n当前查找路径：${OPENCRON_ROOT}\n`);
  console.error('请检查：');
  console.error('  1. opencron 是否正确安装');
  console.error('  2. opencron-config.json 是否存在');
  console.error('  3. 或设置环境变量：OPENCRON_ROOT=/path/to/opencron\n');
  process.exit(1);
}

// 任务分类
function classifyTask(userInput) {
  const input = userInput.toLowerCase().replace(/\s+/g, '');
  
  // 简单任务
  if (input.includes('查看') || input.includes('列表') || input.includes('有哪些')) {
    return { type: 'simple', action: 'list' };
  }
  if (input.includes('删除') || input.includes('移除')) {
    return { type: 'simple', action: 'remove' };
  }
  if (input.includes('日志')) {
    return { type: 'simple', action: 'logs' };
  }
  
  // 复杂任务（添加）
  if (input.includes('添加') || input.includes('创建') || input.includes('提醒') || 
      input.includes('检查') || input.includes('执行')) {
    const knownScripts = ['health-check', 'backup', 'cleanup', 'report'];
    const needsCustomScript = !knownScripts.some(k => input.includes(k));
    
    return {
      type: 'complex',
      action: 'add',
      needsScript: needsCustomScript,
      description: extractTaskDescription(userInput)
    };
  }
  
  return { type: 'unknown', action: 'help' };
}

// 提取任务描述
function extractTaskDescription(userInput) {
  return userInput
    .replace(/每\s*\d*\s*(小时 | 分钟)|每天\s*\d{1,2}[点时]\d{0,2}|每周\s*[一二三四五六日天]\s*\d{1,2}[点时]\d{0,2}|\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}(?::\d{2})?/g, '')
    .replace(/添加 | 创建 | 新建 | 一个 | 任务 | 提醒/g, '')
    .trim() || '未命名任务';
}

// 提取时间（改进中文编码处理）
function extractTimeInfo(userInput) {
  // 保留原始输入（可能有中文字符）
  const input = userInput;
  
  // 每天：多种格式支持
  const dailyPatterns = [
    /每天.*?(\d{1,2})[点時].*?(\d{0,2})/,      // 每天 8 点 30
    /每天.*?(\d{1,2})[点時]/,                    // 每天 8 点
    /每天.*?(\d{1,2}):(\d{2})/                   // 每天 08:30
  ];
  
  for (const pattern of dailyPatterns) {
    const match = input.match(pattern);
    if (match) {
      const hour = match[1].padStart(2, '0');
      const minute = (match[2] || '00').padStart(2, '0');
      return {
        schedule: { type: 'daily', at: `${hour}:${minute}` },
        nextRun: `每天${hour}:${minute}`,
        cronExpression: `${minute} ${hour} * * *`
      };
    }
  }
  
  // 每小时/分钟
  const intervalMatch = input.match(/每.*?(\d+)?(小时 | 分钟)/);
  if (intervalMatch) {
    const num = intervalMatch[1] ? parseInt(intervalMatch[1]) : 1;
    const unit = intervalMatch[2];
    const minutes = unit === '小时' ? num * 60 : num;
    return {
      schedule: { type: 'interval', minutes },
      nextRun: `${minutes}${unit}`,
      cronExpression: `*/${minutes} * * * *`
    };
  }
  
  throw new Error('没找到时间信息');
}

// 格式化错误信息（友好的错误提示）
function formatError(error, userInput) {
  const message = error.message;
  
  if (message === '没找到时间信息') {
    return `
❌ 没找到时间信息

💡 试试这样说：
   • "每天早上 8 点提醒我写日报"
   • "每 5 分钟检查一次服务器"
   • "每周一下午 3 点开会"
   • "明天 10 点提交报告"
`.trim();
  }
  
  if (message.includes('配置文件不存在')) {
    return `
❌ 配置文件不存在

💡 解决方法：
   1. 检查 opencron-config.json 是否存在
   2. 运行：node commands/init.js
`.trim();
  }
  
  if (message.includes('PM2')) {
    return `
❌ PM2 操作失败

💡 解决方法：
   1. 检查 PM2: pm2 --version
   2. 启动 PM2: pm2 start .pm2/ecosystem.config.json
`.trim();
  }
  
  return `❌ 错误：${message}`;
}

// 根据任务类型推荐通知策略
function recommendNotification(userInput) {
  const input = userInput.toLowerCase();
  
  // 监控类 - 安静模式
  if (input.includes('监控') || input.includes('检查') || input.includes('检测')) {
    return {
      start: false,
      success: false,
      error: true,
      reason: '高频任务，只在故障时通知'
    };
  }
  
  // 提醒类 - 强提醒
  if (input.includes('提醒') || input.includes('开会') || input.includes('截止')) {
    return {
      start: true,
      success: false,
      error: true,
      reason: '准时提醒，完成后不打扰'
    };
  }
  
  // 备份类 - 确认模式
  if (input.includes('备份') || input.includes('同步') || input.includes('导出')) {
    return {
      start: false,
      success: true,
      error: true,
      reason: '完成后确认，失败告警'
    };
  }
  
  // 报告类
  if (input.includes('日报') || input.includes('周报') || input.includes('报告')) {
    return {
      start: false,
      success: true,
      error: true,
      reason: '完成后确认，失败告警'
    };
  }
  
  // 默认：提醒类
  return {
    start: true,
    success: false,
    error: true,
    reason: '准时提醒，失败告警'
  };
}

// 生成任务 ID
function generateTaskId(description) {
  return `task-${description.replace(/\s+/g, '-').replace(/[^\w\-]/g, '').toLowerCase().substring(0, 30)}-${Date.now()}`;
}

// 生成脚本
function generateScriptContent(description) {
  const taskId = description.replace(/\s+/g, '-').replace(/[^\w\-]/g, '').toLowerCase();
  
  return `#!/usr/bin/env node
/**
 * ${description}
 * 自动生成时间：${new Date().toLocaleString('zh-CN')}
 */

console.log('[任务开始] ${description}');
console.log('时间：', new Date().toLocaleString('zh-CN'));

// TODO: 在这里添加具体的任务逻辑
console.log('[任务完成] ${description}');
`;
}

// 加载配置
function loadConfig() {
  if (!fs.existsSync(CONFIG_FILE)) {
    throw new Error(`配置文件不存在：${CONFIG_FILE}`);
  }
  return JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf-8'));
}

// 保存配置
function saveConfig(config) {
  fs.writeFileSync(CONFIG_FILE, JSON.stringify(config, null, 2), 'utf-8');
}

// 重启 PM2
function restartScheduler() {
  try {
    execSync('pm2 restart opencron --update-env', { cwd: OPENCRON_ROOT, stdio: 'pipe' });
  } catch (error) {
    console.warn('⚠️  重启失败，请手动执行：pm2 restart opencron\n');
  }
}

// 主处理函数
function handleUserRequest(userInput) {
  const taskClassification = classifyTask(userInput);
  
  if (taskClassification.type === 'simple') {
    return handleSimpleTask(taskClassification.action, userInput);
  } else if (taskClassification.type === 'complex') {
    return handleComplexTask(taskClassification, userInput);
  } else {
    return showHelp();
  }
}

// 处理简单任务
function handleSimpleTask(action, userInput) {
  if (action === 'list') {
    return listTasks();
  }
  return `${action} 功能开发中...`;
}

// 处理复杂任务
function handleComplexTask(classification, userInput) {
  try {
    // 1. 提取时间
    const timeInfo = extractTimeInfo(userInput);
    
    // 2. 推荐通知策略
    const notifyRec = recommendNotification(userInput);
    
    // 3. 生成/使用脚本
    let scriptPath, scriptExists = false;
    if (classification.needsScript) {
      const filename = `${generateTaskId(classification.description)}.js`;
      scriptPath = path.join(OPENCRON_ROOT, 'tasks', filename);
      
      if (!fs.existsSync(scriptPath)) {
        fs.writeFileSync(scriptPath, generateScriptContent(classification.description), 'utf-8');
      } else {
        scriptExists = true;
      }
    }
    
    // 4. 添加配置
    const taskId = classification.needsScript 
      ? path.basename(scriptPath, '.js')
      : 'task-known';
    
    const taskConfig = {
      id: taskId,
      name: classification.description,
      script: classification.needsScript ? `tasks/${path.basename(scriptPath)}` : 'tasks/known.js',
      schedule: timeInfo.schedule,
      enabled: true,
      retryOnError: true,
      maxRetries: 3,
      notification: {
        strategy: 'recommended',
        onStart: { enabled: notifyRec.start },
        onSuccess: { enabled: notifyRec.success },
        onError: { enabled: notifyRec.error }
      }
    };
    
    const config = loadConfig();
    config.tasks.push(taskConfig);
    saveConfig(config);
    restartScheduler();
    
    // 5. 返回结果
    return formatResult(taskConfig, timeInfo, notifyRec, scriptExists);
    
  } catch (error) {
    return `❌ 错误：${error.message}`;
  }
}

// 格式化结果（精简版）
function formatResult(task, timeInfo, notifyRec, scriptExists) {
  return `
✅ 已设置完成！

📋 任务信息：
   - 名称：${task.name}
   - 执行时间：${timeInfo.nextRun}
   - 下次执行：${getNextRun(timeInfo)}
   
🔔 通知设置：
   - ⏰ 启动：${notifyRec.start ? '✅' : '❌'} ${notifyRec.reason}
   - ✅ 完成：${notifyRec.success ? '✅' : '❌'}
   - ❌ 失败：${notifyRec.error ? '✅' : '❌'}（建议保持开启）

${scriptExists ? '⚠️  脚本文件已存在，未覆盖' : '📝 已创建任务脚本'}

需要修改通知设置吗？`.trim();
}

// 计算下次执行时间
function getNextRun(timeInfo) {
  if (timeInfo.schedule.type === 'daily') {
    const [hour, minute] = timeInfo.schedule.at.split(':');
    const now = new Date();
    const next = new Date();
    next.setHours(parseInt(hour), parseInt(minute), 0, 0);
    if (next <= now) next.setDate(next.getDate() + 1);
    return next.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  }
  return `${timeInfo.schedule.minutes}分钟后`;
}

// 查看任务列表
function listTasks() {
  const config = loadConfig();
  if (config.tasks.length === 0) return '📭 暂无任务';
  
  let output = `\n📋 当前共 ${config.tasks.length} 个任务：\n\n`;
  config.tasks.forEach((task, i) => {
    const status = task.enabled ? '🟢' : '🔴';
    output += `${i + 1}. ${status} ${task.name}\n`;
    output += `   调度：${formatSchedule(task.schedule)}\n`;
  });
  
  return output.trim();
}

function formatSchedule(schedule) {
  switch (schedule.type) {
    case 'interval': return `每${schedule.minutes}分钟`;
    case 'daily': return `每天 ${schedule.at}`;
    case 'weekly': return `每周${schedule.day} ${schedule.at}`;
    default: return schedule.type;
  }
}

// 显示帮助
function showHelp() {
  return `
🤖 opencron 智能助手

✅ 支持的说法：
   - "每天早上 8 点提醒我写日报"
   - "每 5 分钟检查一次服务器"
   - "有哪些任务？"
   - "删除监控任务"

💡 我会自动：
   1. 理解你的需求
   2. 推荐合适的通知设置
   3. 创建脚本和配置
   4. 让你无感使用
`.trim();
}

// 运行
const userInput = process.argv.slice(2).join(' ');
if (!userInput) {
  console.log(showHelp());
  process.exit(0);
}

try {
  console.log(handleUserRequest(userInput));
} catch (error) {
  console.log(formatError(error, userInput));
  process.exit(1);
}
