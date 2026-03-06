#!/usr/bin/env node
/**
 * 检测当前运行环境
 * 对应 skill.yml 中的 check-env 命令
 */

import { CONFIG_FILE, TASKS_DIR, LOGS_DIR } from '../utils/config.js';
import fs from 'fs';

console.log('🔍 检测当前运行环境...');
console.log('');

try {
  // 1. 系统信息
  console.log('🖥️  系统信息：');
  console.log(`   - 平台: ${os.platform()} (${os.arch()})`);
  console.log(`   - 系统: ${os.type()} ${os.release()}`);
  console.log(`   - 内存: ${(os.totalmem() / (1024*1024*1024)).toFixed(2)} GB 总计, ${(os.freemem() / (1024*1024*1024)).toFixed(2)} GB 可用`);
  console.log(`   - CPU: ${os.cpus().length} 核心`);
  console.log('');

  // 2. Node.js 环境
  console.log('🟢 Node.js 环境：');
  console.log(`   - 版本: ${process.version}`);
  console.log(`   - 架构: ${process.arch}`);
  console.log(`   - 平台: ${process.platform}`);
  console.log(`   - 运行目录: ${process.cwd()}`);
  console.log(`   - Node.js 路径: ${process.execPath}`);
  console.log('');

  // 3. npm 信息
  console.log('📦 npm 信息：');
  try {
    const npmVersion = execSync('npm --version', { encoding: 'utf-8' }).trim();
    console.log(`   - npm 版本: ${npmVersion}`);
  } catch (error) {
    console.log(`   - npm: ❌ 未安装或不可用 (${error.message})`);
  }
  console.log('');

  // 4. 项目路径
  console.log('📁 项目路径：');
  console.log(`   - 项目根目录: ${PROJECT_ROOT}`);
  console.log(`   - 当前工作目录: ${process.cwd()}`);
  console.log(`   - 脚本目录: ${__dirname}`);
  
  // 检查目录是否存在
  const checkDirs = ['src', 'bin', 'tasks', 'logs'];
  for (const dir of checkDirs) {
    const fullPath = path.join(PROJECT_ROOT, dir);
    const exists = fs.existsSync(fullPath);
    console.log(`   - ${dir}/: ${exists ? '✅' : '❌'} ${fullPath}`);
  }
  console.log('');

  // 5. 可用命令
  console.log('🔧 可用命令：');
  const commandsToCheck = ['node', 'npm', 'git', 'pm2'];
  for (const cmd of commandsToCheck) {
    try {
      execSync(`${cmd} --version`, { stdio: 'pipe' });
      console.log(`   - ${cmd}: ✅`);
    } catch (error) {
      console.log(`   - ${cmd}: ❌ (可能未安装)`);
    }
  }
  console.log('');

  // 6. 网络状态
  console.log('🌐 网络状态：');
  const networkInterfaces = os.networkInterfaces();
  const ipv4Interfaces = Object.values(networkInterfaces).flat()
    .filter(iface => iface.family === 'IPv4' && !iface.internal)
    .map(iface => iface.address);
  
  if (ipv4Interfaces.length > 0) {
    console.log(`   - IP 地址: ${ipv4Interfaces.join(', ')}`);
  } else {
    console.log('   - IP 地址: 未找到外网 IP');
  }
  console.log('');

  // 7. 环境变量
  console.log('ENVIRONMENT VARIABLES:');
  const importantEnvVars = [
    'NODE_ENV',
    'HOME', 
    'USERPROFILE',
    'PATH'
  ];
  
  for (const envVar of importantEnvVars) {
    const value = process.env[envVar];
    if (value) {
      // 隐藏敏感信息
      let displayValue = value;
      if (envVar.toLowerCase().includes('token') || envVar.toLowerCase().includes('key')) {
        displayValue = '***';
      } else if (envVar === 'PATH') {
        displayValue = value.split(path.delimiter).length + ' 项';
      } else if (value.length > 50) {
        displayValue = value.substring(0, 50) + '...';
      }
      console.log(`   - ${envVar}: ${displayValue}`);
    } else {
      console.log(`   - ${envVar}: 未设置`);
    }
  }
  console.log('');

  // 8. 项目特定信息
  console.log('🎯 项目特定信息：');
  const configFile = path.join(PROJECT_ROOT, 'opencron-config.json');
  if (fs.existsSync(configFile)) {
    const config = JSON.parse(fs.readFileSync(configFile, 'utf-8'));
    console.log(`   - 任务数量: ${config.tasks.length}`);
    console.log(`   - 配置版本: ${config.version}`);
    
    // 统计任务状态
    const enabledCount = config.tasks.filter(t => t.enabled).length;
    const disabledCount = config.tasks.length - enabledCount;
    console.log(`   - 启用任务: ${enabledCount}, 禁用任务: ${disabledCount}`);
  } else {
    console.log('   - 配置文件: ❌ 不存在');
  }

  const packageJson = path.join(PROJECT_ROOT, 'package.json');
  if (fs.existsSync(packageJson)) {
    const pkg = JSON.parse(fs.readFileSync(packageJson, 'utf-8'));
    console.log(`   - 项目名称: ${pkg.name}`);
    console.log(`   - 项目版本: ${pkg.version}`);
    console.log(`   - 依赖数量: ${Object.keys(pkg.dependencies || {}).length}`);
  } else {
    console.log('   - package.json: ❌ 不存在');
  }
  console.log('');

  // 9. 权限检查
  console.log('🔑 权限检查：');
  try {
    // 尝试创建临时文件来检查写入权限
    const tempFile = path.join(PROJECT_ROOT, '.permission-test');
    fs.writeFileSync(tempFile, 'test');
    fs.unlinkSync(tempFile);
    console.log('   - 项目目录: ✅ 可读写');
  } catch (error) {
    console.log(`   - 项目目录: ❌ 无写入权限 (${error.message})`);
  }
  console.log('');

  // 10. 建议
  console.log('💡 环境建议：');
  if (parseInt(process.version.split('.')[0].substring(1)) < 18) {
    console.log('   - 建议升级 Node.js 到 18+ 版本');
  }
  
  const freeMemGB = os.freemem() / (1024*1024*1024);
  if (freeMemGB < 1) {
    console.log('   - 建议释放更多内存 (当前可用 < 1GB)');
  }
  
  if (!fs.existsSync(path.join(PROJECT_ROOT, 'node_modules'))) {
    console.log('   - 建议安装依赖: npm install');
  }
  
  if (!fs.existsSync(path.join(PROJECT_ROOT, 'logs'))) {
    console.log('   - 建议创建日志目录');
  }

  console.log('');
  console.log('✅ 环境检测完成！');

} catch (error) {
  console.error('❌ 环境检测失败：', error.message);
  process.exit(1);
}
