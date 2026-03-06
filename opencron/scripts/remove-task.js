#!/usr/bin/env node
/**
 * 删除任务
 * 对应 skill.yml 中的 remove-task 命令
 */

import { CONFIG_FILE } from "../utils/config.js";
import fs from "fs";

const taskId = process.argv[2];

if (!taskId) {
  console.error('❌ 请提供任务 ID');
  console.log('用法: node remove-task.sh <task-id>');
  process.exit(1);
}

try {
  // 读取现有配置
  const config = JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf-8'));
  
  // 查找任务
  const taskIndex = config.tasks.findIndex(t => t.id === taskId);
  if (taskIndex === -1) {
    console.error(`❌ 未找到任务：${taskId}`);
    console.log('当前可用任务：');
    config.tasks.forEach(t => console.log(`  - ${t.id}`));
    process.exit(1);
  }
  
  const task = config.tasks[taskIndex];
  
  // 删除任务
  config.tasks.splice(taskIndex, 1);
  
  // 写回配置文件
  fs.writeFileSync(CONFIG_FILE, JSON.stringify(config, null, 2));
  
  console.log(`✅ 任务删除成功：${taskId}`);
  console.log('📋 已删除的任务：');
  console.log(`${task.id} - ${task.name} (${task.script})`);
  
  console.log('');
  console.log('💡 提醒：配置已更新，调度器会自动加载新配置');
} catch (error) {
  console.error('❌ 删除任务失败：', error.message);
  process.exit(1);
}