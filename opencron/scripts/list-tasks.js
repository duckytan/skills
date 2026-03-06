#!/usr/bin/env node
/**
 * 查看所有任务
 * 对应 skill.yml 中的 list-tasks 命令
 */

import { CONFIG_FILE } from "../utils/config.js";
import fs from "fs";

try {
  const config = JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf-8'));
  
  console.log('📋 当前任务列表：');
  console.log('');
  
  config.tasks.forEach((task, index) => {
    const status = task.enabled ? '✅ 启用' : '❌ 禁用';
    const scheduleInfo = formatSchedule(task.schedule);
    
    console.log(`${index + 1}. ${task.id} - ${task.name}`);
    console.log(`   说明：${task.description || '无'}`);
    console.log(`   执行：${task.script}`);
    console.log(`   调度：${scheduleInfo}`);
    console.log(`   状态：${status}`);
    console.log('');
  });
  
  console.log(`总计：${config.tasks.length} 个任务`);
} catch (error) {
  console.error('❌ 查看任务失败：', error.message);
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