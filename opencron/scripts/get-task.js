#!/usr/bin/env node
/**
 * 查看任务详情
 * 对应 skill.yml 中的 get-task 命令
 */

import { CONFIG_FILE } from "../utils/config.js";
import fs from "fs";

const taskId = process.argv[2];

if (!taskId) {
  console.error('❌ 请提供任务 ID');
  console.log('用法: node get-task.sh <task-id>');
  process.exit(1);
}

try {
  const config = JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf-8'));
  const task = config.tasks.find(t => t.id === taskId);
  
  if (!task) {
    console.error(`❌ 未找到任务 ID: ${taskId}`);
    console.log('可用的任务：');
    config.tasks.forEach(t => console.log(`  - ${t.id}`));
    process.exit(1);
  }
  
  console.log(`📋 任务详情：${task.id}`);
  console.log('================================');
  console.log(`名称：${task.name}`);
  console.log(`说明：${task.description || '无'}`);
  console.log(`脚本：${task.script}`);
  console.log(`调度：${formatSchedule(task.schedule)}`);
  console.log(`状态：${task.enabled ? '✅ 启用' : '❌ 禁用'}`);
  console.log(`重试：${task.retryOnError ? '✅ 是' : '❌ 否'}`);
  console.log(`最大重试：${task.maxRetries || 3} 次`);
  if (task.timeout) {
    console.log(`超时：${task.timeout} 毫秒`);
  }
  
  console.log('');
  console.log('📋 配置详情：');
  console.log(JSON.stringify(task, null, 2));
} catch (error) {
  console.error('❌ 查看任务详情失败：', error.message);
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