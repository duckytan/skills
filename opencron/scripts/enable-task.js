#!/usr/bin/env node
/**
 * 启用任务
 * 对应 skill.yml 中的 enable-task 命令
 */

import { CONFIG_FILE } from "../utils/config.js";
import fs from "fs";

const taskId = process.argv[2];

if (!taskId) {
  console.error('❌ 请提供任务 ID');
  console.log('用法: node enable-task.sh <task-id>');
  process.exit(1);
}

try {
  // 读取现有配置
  const config = JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf-8'));
  
  // 查找任务
  const taskIndex = config.tasks.findIndex(t => t.id === taskId);
  if (taskIndex === -1) {
    console.error(`❌ 未找到任务：${taskId}`);
    process.exit(1);
  }
  
  // 启用任务
  const oldEnabled = config.tasks[taskIndex].enabled;
  config.tasks[taskIndex].enabled = true;
  
  // 写回配置文件
  fs.writeFileSync(CONFIG_FILE, JSON.stringify(config, null, 2));
  
  if (oldEnabled) {
    console.log(`ℹ️  任务 ${taskId} 本来就已经启用了`);
  } else {
    console.log(`✅ 任务启用成功：${taskId}`);
  }
  
  console.log('📋 任务状态：');
  console.log(`${config.tasks[taskIndex].id} - ${config.tasks[taskIndex].name} - ✅ 启用`);
  
  console.log('');
  console.log('💡 提醒：配置已更新，调度器会自动加载新配置');
} catch (error) {
  console.error('❌ 启用任务失败：', error.message);
  process.exit(1);
}