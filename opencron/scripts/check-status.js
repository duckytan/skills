#!/usr/bin/env node
/**
 * 检查调度器状态
 * 对应 skill.yml 中的 check-status 命令
 */

import { CONFIG_FILE, TASKS_DIR, LOGS_DIR } from '../utils/config.js';
import { CONFIG_FILE } from "../utils/config.js";
import fs from "fs";

try {
  console.log('📋 Opencron 调度器状态检查');
  console.log('================================');
  
  // 检查 PM2 状态
  console.log('✅ PM2 进程状态：');
  try {
    const { execSync } = await import('child_process');
    const pm2Status = execSync('pm2 list opencron', { encoding: 'utf-8' });
    console.log(pm2Status);
  } catch {
    console.log('   ⚠️  PM2 未运行或未安装');
    console.log('   💡 使用 "启动调度器" 命令来启动');
  }
  
  // 检查配置文件
  console.log('✅ 配置文件检查：');
  if (fs.existsSync(CONFIG_FILE)) {
    const config = JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf-8'));
    console.log(`   ✓ 配置文件存在，包含 ${config.tasks.length} 个任务`);
    
    // 统计任务状态
    const enabledCount = config.tasks.filter(t => t.enabled).length;
    const disabledCount = config.tasks.length - enabledCount;
    console.log(`   ✓ ${enabledCount} 个启用，${disabledCount} 个禁用`);
  } else {
    console.log('   ❌ 配置文件不存在');
  }
  
  // 检查日志目录
  console.log('');
  console.log('✅ 日志系统检查：');
  if (fs.existsSync(LOGS_DIR)) {
    const logFiles = fs.readdirSync(LOGS_DIR);
    console.log(`   ✓ 日志目录存在，包含 ${logFiles.length} 个日志文件`);
    
    // 显示最新的日志文件
    if (logFiles.length > 0) {
      console.log('   最近日志文件：');
      logFiles.slice(-3).forEach(file => {
        const stats = fs.statSync(path.join(LOGS_DIR, file));
        const size = stats.size;
        const modified = stats.mtime.toLocaleString();
        console.log(`     - ${file} (${size} 字节, ${modified})`);
      });
    }
  } else {
    console.log('   ❌ 日志目录不存在');
  }
  
  // 检查任务脚本
  console.log('');
  console.log('✅ 任务脚本检查：');
  if (fs.existsSync(TASKS_DIR)) {
    const taskFiles = fs.readdirSync(TASKS_DIR).filter(f => f.endsWith('.js'));
    console.log(`   ✓ 任务脚本目录存在，包含 ${taskFiles.length} 个脚本`);
    
    if (fs.existsSync(CONFIG_FILE)) {
      const config = JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf-8'));
      const configuredScripts = config.tasks.map(t => t.script.replace('tasks/', ''));
      
      // 检查配置的任务脚本是否存在
      const missingScripts = configuredScripts.filter(script => !taskFiles.includes(script));
      if (missingScripts.length > 0) {
        console.log(`   ⚠️  警告：配置中指定但不存在的脚本：${missingScripts.join(', ')}`);
      } else {
        console.log('   ✓ 所有配置的任务脚本都存在');
      }
    }
  } else {
    console.log('   ❌ 任务脚本目录不存在');
  }
  
  // 任务统计
  if (fs.existsSync(CONFIG_FILE)) {
    console.log('');
    console.log('📋 任务详情：');
    const config = JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf-8'));
    
    config.tasks.forEach((task, index) => {
      const status = task.enabled ? '✅' : '❌';
      console.log(`   ${index + 1}. ${status} ${task.id} - ${task.name} (${task.script})`);
      console.log(`       调度: ${formatSchedule(task.schedule)} | 重试: ${task.retryOnError ? '是' : '否'}`);
    });
  }
  
  console.log('');
  console.log('✅ 状态检查完成');
  console.log('💡 提醒：调度器会自动加载配置文件的更改');

} catch (error) {
  console.error('❌ 状态检查失败：', error.message);
  process.exit(1);
}

function formatSchedule(schedule) {
  switch (schedule.type) {
    case 'hourly':
      return `每小时 (分钟: ${schedule.minutes || 0})`;
    case 'daily':
      return `每天 ${schedule.at}`;
    case 'weekly':
      return `每周 ${schedule.day} ${schedule.at}`;
    case 'interval':
      return `每 ${schedule.minutes} 分钟`;
    default:
      return `${schedule.type} (${JSON.stringify(schedule)})`;
  }
}