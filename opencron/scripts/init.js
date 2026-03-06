#!/usr/bin/env node
/**
 * 初始化 opencron 系统
 * 对应 skill.yml 中的 init 命令
 */

import { CONFIG_FILE, TASKS_DIR, LOGS_DIR } from '../utils/config.js';
import fs from 'fs';

console.log('🚀 正在初始化 Opencron 系统...');
console.log('');

try {
  // 1. 检查 Node.js 版本
  console.log('🔍 检查 Node.js 版本...');
  const nodeVersion = process.version;
  console.log(`   ✅ Node.js: ${nodeVersion}`);
  
  if (parseInt(nodeVersion.split('.')[0].substring(1)) < 18) {
    console.warn('   ⚠️  建议使用 Node.js 18+ 版本');
  }

  // 2. 检查项目文件
  console.log('');
  console.log('🔍 检查项目文件...');
  const requiredFiles = [
    'package.json',
    'opencron-config.json', 
    'src/scheduler.js',
    'bin/opencron.js',
    'tasks/'
  ];
  
  const missingFiles = [];
  requiredFiles.forEach(file => {
    const fullPath = path.join(PROJECT_ROOT, file);
    if (fs.existsSync(fullPath)) {
      console.log(`   ✅ ${file}`);
    } else {
      console.log(`   ❌ ${file}`);
      missingFiles.push(file);
    }
  });

  if (missingFiles.length > 0) {
    console.error('');
    console.error('❌ 项目文件缺失：', missingFiles.join(', '));
    console.error('💡 请确保在正确的项目目录下运行');
    process.exit(1);
  }

  // 3. 检查依赖
  console.log('');
  console.log('🔍 检查依赖...');
  try {
    const packageJson = JSON.parse(fs.readFileSync(path.join(PROJECT_ROOT, 'package.json'), 'utf-8'));
    console.log(`   ✅ package.json 读取成功 (opencron-system v${packageJson.version})`);
  } catch (error) {
    console.error('   ❌ package.json 读取失败');
    process.exit(1);
  }

  // 4. 检查配置文件
  console.log('');
  console.log('🔍 检查配置文件...');
  const configFile = path.join(PROJECT_ROOT, 'opencron-config.json');
  if (fs.existsSync(configFile)) {
    const config = JSON.parse(fs.readFileSync(configFile, 'utf-8'));
    console.log(`   ✅ 配置文件存在，包含 ${config.tasks.length} 个任务`);
    
    // 验证配置格式
    if (!config.version || !config.tasks) {
      console.warn('   ⚠️  配置文件格式可能不完整');
    } else {
      console.log(`   ✅ 配置文件格式正确 (v${config.version})`);
    }
  } else {
    console.error('   ❌ 配置文件不存在');
    process.exit(1);
  }

  // 5. 检查日志目录
  console.log('');
  console.log('🔍 检查日志目录...');
  const logsDir = path.join(PROJECT_ROOT, 'logs');
  if (!fs.existsSync(logsDir)) {
    fs.mkdirSync(logsDir, { recursive: true });
    console.log('   ✅ 创建日志目录');
  } else {
    console.log('   ✅ 日志目录已存在');
  }

  // 6. 检查任务脚本目录
  console.log('');
  console.log('🔍 检查任务脚本...');
  const tasksDir = path.join(PROJECT_ROOT, 'tasks');
  if (fs.existsSync(tasksDir)) {
    const taskFiles = fs.readdirSync(tasksDir).filter(f => f.endsWith('.js'));
    console.log(`   ✅ 任务目录存在，包含 ${taskFiles.length} 个脚本`);
  } else {
    console.error('   ❌ 任务脚本目录不存在');
    process.exit(1);
  }

  console.log('');
  console.log('🎉 Opencron 系统初始化完成！');
  console.log('');
  console.log('📋 系统状态：');
  console.log(`   - Node.js: ${nodeVersion}`);
  console.log(`   - 项目版本: v${require(path.join(PROJECT_ROOT, 'package.json')).version}`);
  console.log(`   - 任务数量: ${JSON.parse(fs.readFileSync(configFile, 'utf-8')).tasks.length}`);
  console.log(`   - 配置文件: ✅`);
  console.log(`   - 日志目录: ✅`);
  console.log(`   - 任务脚本: ✅`);
  console.log('');
  console.log('💡 使用建议：');
  console.log('   - 查看任务: node skills/opencron/scripts/list-tasks.js');
  console.log('   - 启动调度器: node src/scheduler.js');
  console.log('   - 健康检查: node bin/opencron.js doctor');

} catch (error) {
  console.error('❌ 初始化失败：', error.message);
  process.exit(1);
}
