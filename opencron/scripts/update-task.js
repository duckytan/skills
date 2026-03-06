#!/usr/bin/env node
/**
 * 修改任务
 * 对应 skill.yml 中的 update-task 命令
 */

import { CONFIG_FILE } from "../utils/config.js";
import fs from "fs";

const taskId = process.argv[2];
const updateJson = process.argv[3];

if (!taskId || !updateJson) {
  console.error('❌ 请提供任务 ID 和更新配置');
  console.log('用法: node update-task.sh <task-id> \'{"schedule":{"type":"daily","at":"03:00"}}\'');
  process.exit(1);
}

try {
  const updates = JSON.parse(updateJson);
  
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
  
  const oldTask = { ...config.tasks[taskIndex] };
  
  // 更新任务（只更新提供的字段）
  config.tasks[taskIndex] = { ...config.tasks[taskIndex], ...updates };
  
  // 写回配置文件
  fs.writeFileSync(CONFIG_FILE, JSON.stringify(config, null, 2));
  
  console.log(`✅ 任务更新成功：${taskId}`);
  console.log('📋 更新前：');
  console.log(JSON.stringify(oldTask, null, 2));
  console.log('');
  console.log('📋 更新后：');
  console.log(JSON.stringify(config.tasks[taskIndex], null, 2));
  
  console.log('');
  console.log('💡 提醒：配置已更新，调度器会自动加载新配置');
} catch (error) {
  console.error('❌ 修改任务失败：', error.message);
  process.exit(1);
}