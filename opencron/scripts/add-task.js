#!/usr/bin/env node
/**
 * 添加新任务
 * 对应 skill.yml 中的 add-task 命令
 */

import { CONFIG_FILE, TASKS_DIR, LOGS_DIR } from '../utils/config.js';
import fs from 'fs';

// 从命令行参数获取任务配置
const taskJson = process.argv[2];

if (!taskJson) {
  console.error('❌ 请提供任务配置 JSON');
  console.log('用法: node add-task.sh \'{"id":"test","name":"测试任务","script":"tasks/test.js","schedule":{"type":"hourly"}}\'');
  process.exit(1);
}

try {
  const newTask = JSON.parse(taskJson);
  
  // 验证必要字段
  if (!newTask.id || !newTask.name || !newTask.script || !newTask.schedule) {
    console.error('❌ 任务配置缺少必要字段：id, name, script, schedule');
    process.exit(1);
  }
  
  // 默认值
  if (newTask.enabled === undefined) newTask.enabled = true;
  if (newTask.retryOnError === undefined) newTask.retryOnError = true;
  if (newTask.maxRetries === undefined) newTask.maxRetries = 3;
  
  // 读取现有配置
  const config = JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf-8'));
  
  // 检查任务 ID 是否已存在
  const existingTask = config.tasks.find(t => t.id === newTask.id);
  if (existingTask) {
    console.error(`❌ 任务 ID 已存在：${newTask.id}`);
    process.exit(1);
  }
  
  // 添加新任务
  config.tasks.push(newTask);
  
  // 写回配置文件
  fs.writeFileSync(CONFIG_FILE, JSON.stringify(config, null, 2));
  
  console.log(`✅ 任务添加成功：${newTask.id}`);
  console.log('📋 任务详情：');
  console.log(JSON.stringify(newTask, null, 2));
  
  console.log('');
  console.log('💡 提醒：配置已更新，调度器会自动加载新配置');
} catch (error) {
  console.error('❌ 添加任务失败：', error.message);
  process.exit(1);
}