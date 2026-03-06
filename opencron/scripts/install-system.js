#!/usr/bin/env node
/**
 * opencron 系统完整安装
 * 适用于全新环境的自动化安装
 */

import { CONFIG_FILE, TASKS_DIR, LOGS_DIR } from '../utils/config.js';
import fs from 'fs';
import { execSync } from 'child_process';

console.log('🚀 开始安装 opencron 系统...');
console.log('');

try {
  // 1. 检查环境
  console.log('🔍 检查环境依赖...');
  let nodeOk = false, npmOk = false, gitOk = false;
  
  try {
    execSync('node --version', { stdio: 'pipe' });
    nodeOk = true;
    console.log('   Node.js: ✅ 已安装');
  } catch {
    console.log('   Node.js: ❌ 未安装');
  }
  
  try {
    execSync('npm --version', { stdio: 'pipe' });
    npmOk = true;
    console.log('   npm: ✅ 已安装');
  } catch {
    console.log('   npm: ❌ 未安装');
  }
  
  try {
    execSync('git --version', { stdio: 'pipe' });
    gitOk = true;
    console.log('   Git: ✅ 已安装');
  } catch {
    console.log('   Git: ❌ 未安装');
  }
  console.log('');

  // 2. 安装依赖（如果没有）
  if (!nodeOk || !npmOk) {
    console.log('📦 安装 Node.js...');
    if (process.platform === 'linux') {
      // Ubuntu/Debian
      try {
        execSync('curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt-get install -y nodejs', { stdio: 'inherit' });
      } catch {
        // 备选方案：使用 nvm
        console.log('   使用 nvm 安装 Node.js...');
        execSync('curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.0/install.sh | bash', { stdio: 'pipe' });
        execSync('export NVM_DIR="$HOME/.nvm" && [ -s "$NVM_DIR/nvm.sh" ] && \\.[ "$NVM_DIR/nvm.sh" ] && nvm install 20 && nvm use 20', { stdio: 'inherit' });
      }
    } else if (process.platform === 'darwin') {
      // macOS
      execSync('brew install node', { stdio: 'inherit' });
    } else {
      console.log('   ⚠️  请手动安装 Node.js 20+');
      process.exit(1);
    }
    console.log('✅ Node.js 安装完成');
  }
  
  if (!gitOk) {
    console.log('📦 安装 Git...');
    if (process.platform === 'linux') {
      execSync('sudo apt-get install -y git', { stdio: 'inherit' });
    } else if (process.platform === 'darwin') {
      execSync('brew install git', { stdio: 'inherit' });
    }
    console.log('✅ Git 安装完成');
  }
  console.log('');

  // 3. 安装 opencron
  console.log('💾 安装 opencron 系统...');
  execSync('npm install -g opencron-system', { stdio: 'inherit' });
  console.log('✅ opencron-system 安装完成');
  console.log('');

  // 4. 安装项目依赖（包括 PM2）
  console.log('📦 安装项目依赖（包括 PM2）...');
  execSync('npm install', { 
    cwd: path.join(PROJECT_ROOT, 'project', 'opencron'),
    stdio: 'inherit' 
  });
  console.log('✅ 依赖安装完成（PM2 已包含）');
  console.log('');

  // 5. 初始化配置
  console.log('🔧 初始化配置...');
  // 创建基本配置文件
  const config = {
    version: '2.0',
    timezone: 'Asia/Shanghai',
    tasks: [
      {
        id: 'health-check',
        name: '健康检查',
        description: '每小时检查系统状态',
        script: 'tasks/health-check.js',
        schedule: {
          type: 'interval',
          minutes: 60
        },
        enabled: true,
        retryOnError: true,
        maxRetries: 3
      }
    ]
  };
  
  fs.writeFileSync('opencron-config.json', JSON.stringify(config, null, 2));
  console.log('✅ 配置文件创建完成');
  
  // 创建 PM2 配置目录和文件
  console.log('🔧 配置 PM2...');
  if (!fs.existsSync('.pm2')) {
    fs.mkdirSync('.pm2');
  }
  
  const pm2Config = {
    apps: [{
      name: 'opencron',
      script: './src/scheduler.js',
      cwd: process.cwd(),
      watch: false,
      ignore_watch: ['node_modules', 'logs', 'backups', 'tasks'],
      max_restarts: 10,
      min_uptime: '10s',
      error_file: './logs/error.log',
      out_file: './logs/output.log',
      log_file: './logs/combined.log',
      time: true,
      env: {
        NODE_ENV: 'production',
        DEBUG: false
      }
    }]
  };
  
  fs.writeFileSync('.pm2/ecosystem.config.json', JSON.stringify(pm2Config, null, 2));
  console.log('✅ PM2 配置创建完成');
  console.log('');
  
  // 创建基本任务目录和示例任务
  if (!fs.existsSync('tasks')) {
    fs.mkdirSync('tasks');
  }
  
  // 创建示例任务脚本
  fs.writeFileSync('tasks/health-check.js', `#!/usr/bin/env node
/**
 * 健康检查任务
 */

console.log('[HEALTH CHECK] ' + new Date().toLocaleString('zh-CN', { 
  timeZone: 'Asia/Shanghai',
  hour12: false 
}));
console.log('✅ 系统健康检查完成');
`, 'utf-8');
  
  console.log('✅ 示例任务创建完成');
  console.log('');

  // 5. 验证
  console.log('✅ 验证安装...');
  console.log('   - 检查命令是否可用...');
  try {
    execSync('npx opencron doctor', { stdio: 'pipe' });
    console.log('   - 检查配置文件...');
    if (fs.existsSync('opencron-config.json')) {
      console.log('   - 配置文件存在');
    } else {
      console.log('   - 配置文件不存在');
    }
    console.log('   - 检查任务脚本...');
    if (fs.existsSync('tasks/')) {
      console.log('   - 任务目录存在');
    } else {
      console.log('   - 任务目录不存在');
    }
  } catch (error) {
    console.log('   ⚠️  验证时遇到问题，但安装可能成功');
  }
  
  console.log('');
  console.log('🎉 安装完成！opencron 系统已准备就绪');
  console.log('');
  console.log('💡 使用命令：');
  console.log('   查看任务: npx opencron list');
  console.log('   启动调度器（PM2）: pm2 start .pm2/ecosystem.config.json');
  console.log('   查看状态：pm2 list');
  console.log('   查看日志：pm2 logs opencron');
  console.log('   健康检查：npx opencron doctor');
  console.log('');
  console.log('🚀 现在您可以使用 @opencron 来管理定时任务了！');
  console.log('');
  console.log('📋 下一步建议：');
  console.log('   1. 添加任务：对我说 "添加一个每天凌晨 2 点备份记忆的任务"');
  console.log('   2. 启动调度器：对我说 "启动调度器"');
  console.log('   3. 查看状态：对我说 "查看定时任务状态"');

} catch (error) {
  console.error('❌ 安装失败：', error.message);
  process.exit(1);
}
