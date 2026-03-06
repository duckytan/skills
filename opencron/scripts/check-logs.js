#!/usr/bin/env node
/**
 * 查看任务日志
 * 对应 skill.yml 中的 check-logs 命令
 */

import { CONFIG_FILE, TASKS_DIR, LOGS_DIR } from '../utils/config.js';
import fs from 'fs';

// 获取任务名或查看所有日志
const taskName = process.argv[2];
const lines = parseInt(process.argv[3]) || 50; // 默认显示 50 行

try {
  if (taskName) {
    // 查看特定任务日志
    const logFiles = fs.readdirSync(LOGS_DIR);
    const taskLogFile = logFiles.find(file => 
      file.startsWith(`task-${taskName}`) || 
      file.includes(taskName)
    );
    
    if (!taskLogFile) {
      console.error(`❌ 未找到任务 "${taskName}" 的日志文件`);
      console.log('可用的日志文件：');
      logFiles.forEach(file => console.log(`  - ${file}`));
      process.exit(1);
    }
    
    const logContent = fs.readFileSync(logPath, 'utf-8');
    const logLines = logContent.split('\n').filter(line => line.trim());
    const recentLines = logLines.slice(-lines);
    
    console.log(`📋 任务 "${taskName}" 最近 ${recentLines.length} 条日志：`);
    console.log('================================');
    recentLines.forEach(line => console.log(line));
  } else {
    // 查看调度器日志
    if (!fs.existsSync(schedulerLog)) {
      console.log('📋 日志目录中没有找到日志文件');
      const logFiles = fs.readdirSync(LOGS_DIR);
      console.log('当前日志文件：');
      logFiles.forEach(file => console.log(`  - ${file}`));
      process.exit(0);
    }
    
    const logContent = fs.readFileSync(schedulerLog, 'utf-8');
    const logLines = logContent.split('\n').filter(line => line.trim());
    const recentLines = logLines.slice(-lines);
    
    console.log(`📋 调度器最近 ${recentLines.length} 条日志：`);
    console.log('================================');
    recentLines.forEach(line => console.log(line));
  }
} catch (error) {
  console.error('❌ 查看日志失败：', error.message);
  process.exit(1);
}
