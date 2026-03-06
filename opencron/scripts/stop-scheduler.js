#!/usr/bin/env node
/**
 * 停止调度器（使用 PM2 API）
 * 对应 skill.yml 中的 stop-scheduler 命令
 */

import path from 'path';
import { fileURLToPath } from 'url';
import { createRequire } from 'module';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.join(__dirname, "..", "..", "..", "..");
const OPENCRON_ROOT = path.join(PROJECT_ROOT, 'project', 'opencron');

// 从 opencron 项目的 node_modules 加载 PM2
const require = createRequire(path.join(OPENCRON_ROOT, 'package.json'));
const pm2 = require('pm2');

console.log('🛑 使用 PM2 停止 opencron 调度器...\n');

// 连接到 PM2 daemon
pm2.connect((err) => {
  if (err) {
    console.error('❌ 连接 PM2 失败:', err.message);
    process.exit(1);
  }

  console.log('✅ 已连接到 PM2\n');
  console.log('🛑 停止 opencron 进程...\n');

  // 停止应用
  pm2.stop('opencron', (err) => {
    if (err) {
      console.error('❌ 停止失败:', err.message);
      pm2.disconnect();
      process.exit(1);
    }

    console.log('✅ opencron 调度器已停止！\n');
    
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
      }
      
      console.log('\n💡 常用命令：');
      console.log('   启动：node .opencode/skills/opencron/scripts/start-scheduler.js');
      console.log('   重启：node .opencode/skills/opencron/scripts/start-scheduler.js');
      console.log('   查看日志：node .opencode/skills/opencron/scripts/check-logs.js');
      
      pm2.disconnect();
    });
  });
});
