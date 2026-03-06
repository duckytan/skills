#!/usr/bin/env node
/**
 * 启动调度器（使用 PM2 API）
 * 对应 skill.yml 中的 start-scheduler 命令
 */

import path from 'path';
import { fileURLToPath } from 'url';
import { createRequire } from 'module';
import fs from 'fs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.join(__dirname, "..", "..", "..", "..");
const OPENCRON_ROOT = path.join(PROJECT_ROOT, 'project', 'opencron');

// 从 opencron 项目的 node_modules 加载 PM2
const require = createRequire(path.join(OPENCRON_ROOT, 'package.json'));
const pm2 = require('pm2');

console.log('🚀 使用 PM2 启动 opencron 调度器...\n');

// 检查配置文件
const pm2ConfigPath = path.join(OPENCRON_ROOT, '.pm2', 'ecosystem.config.json');
if (!fs.existsSync(pm2ConfigPath)) {
  console.error('❌ PM2 配置文件不存在');
  console.error('   位置：', pm2ConfigPath);
  process.exit(1);
}

const config = JSON.parse(fs.readFileSync(pm2ConfigPath, 'utf-8'));

// 连接到 PM2 daemon
pm2.connect((err) => {
  if (err) {
    console.error('❌ 连接 PM2 失败:', err.message);
    process.exit(1);
  }

  console.log('✅ 已连接到 PM2\n');
  console.log('🚀 启动 opencron 进程...\n');

  // 启动应用
  pm2.start(config, (err, apps) => {
    if (err) {
      console.error('❌ 启动失败:', err.message);
      pm2.disconnect();
      process.exit(1);
    }

    console.log('✅ opencron 调度器已启动！\n');
    
    // 显示状态
    pm2.list((err, list) => {
      if (err) {
        pm2.disconnect();
        process.exit(1);
      }
      
      const opencronApp = list.find(app => app.name === 'opencron');
      if (opencronApp) {
        console.log('📊 进程状态：');
        console.log(`   ID: ${opencronApp.pm_id || opencronApp.pm2_env?.pm_id}`);
        console.log(`   状态：${opencronApp.status || opencronApp.pm2_env?.status}`);
        console.log(`   PID: ${opencronApp.pid || opencronApp.pm2_env?.pid}`);
        console.log(`   运行时间：${opencronApp.pm2_env?.uptime || '刚启动'}`);
        console.log(`   内存：${(opencronApp.monit?.memory / 1024 / 1024).toFixed(1)} MB`);
      }
      
      console.log('\n💡 常用命令：');
      console.log('   查看日志：node .opencode/skills/opencron/scripts/check-logs.js');
      console.log('   重启：node .opencode/skills/opencron/scripts/start-scheduler.js');
      console.log('   停止：node .opencode/skills/opencron/scripts/stop-scheduler.js');
      console.log('   状态：node .opencode/skills/opencron/scripts/check-status.js');
      
      pm2.disconnect();
    });
  });
});