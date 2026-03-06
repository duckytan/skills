#!/usr/bin/env node
/**
 * Opencron 系统自我诊断
 * 对应 skill.yml 中的 doctor 命令
 */

import { CONFIG_FILE, TASKS_DIR, LOGS_DIR } from '../utils/config.js';
import fs from 'fs';

console.log('🏥 Opencron 系统健康检查');
console.log('================================');
console.log('');

let issuesFound = 0;
const report = [];

try {
  // 1. 基础环境检查
  console.log('🔍 1. 基础环境检查');
  console.log('-------------------');
  
  // Node.js 检查
  const nodeVersion = process.version;
  const nodeOk = parseInt(nodeVersion.split('.')[0].substring(1)) >= 18;
  console.log(`   Node.js: ${nodeVersion} ${nodeOk ? '✅' : '⚠️'}`);
  if (!nodeOk) {
    report.push(`Node.js 版本过低 (${nodeVersion})，建议 18+`);
    issuesFound++;
  }

  // npm 检查
  try {
    const npmVersion = execSync('npm --version', { encoding: 'utf-8' }).trim();
    console.log(`   npm: v${npmVersion} ✅`);
  } catch (e) {
    console.log('   npm: ❌ (未安装或不可用)');
    report.push('npm 未安装或不可用');
    issuesFound++;
  }
  console.log('');

  // 2. 项目文件检查
  console.log('📋 2. 项目文件检查');
  console.log('-------------------');
  
  const requiredFiles = [
    { path: 'package.json', desc: '项目配置' },
    { path: 'opencron-config.json', desc: '任务配置' },
    { path: 'src/scheduler.js', desc: '调度器核心' },
    { path: 'bin/opencron.js', desc: 'CLI 入口' },
    { path: 'tasks/', desc: '任务脚本目录' },
  ];

  for (const file of requiredFiles) {
    const fullPath = path.join(PROJECT_ROOT, file.path);
    const exists = fs.existsSync(fullPath);
    const isDir = exists && fs.lstatSync(fullPath).isDirectory();
    console.log(`   ${file.desc} (${file.path}): ${exists ? '✅' : '❌'} ${isDir ? '目录' : exists ? '文件' : ''}`);
    
    if (!exists) {
      report.push(`${file.desc} 缺失: ${file.path}`);
      issuesFound++;
    }
  }
  
  // 检查 logs 目录，如果不存在就创建
  const logsPath = path.join(PROJECT_ROOT, 'logs');
  const logsExist = fs.existsSync(logsPath);
  console.log(`   日志目录 (logs/): ${logsExist ? '✅ 目录' : '⚠️  目录 (不存在，将自动创建)'}`);
  if (!logsExist) {
    fs.mkdirSync(logsPath, { recursive: true });
    console.log('   📁 已自动创建日志目录');
  }
  console.log('');

  // 3. 配置文件验证
  console.log('⚙️  3. 配置文件验证');
  console.log('-------------------');
  
  const configFile = path.join(PROJECT_ROOT, 'opencron-config.json');
  if (fs.existsSync(configFile)) {
    try {
      const config = JSON.parse(fs.readFileSync(configFile, 'utf-8'));
      
      if (config.version) {
        console.log(`   配置版本: v${config.version} ✅`);
      } else {
        console.log('   配置版本: ⚠️  (未指定版本)');
      }
      
      if (config.tasks && Array.isArray(config.tasks)) {
        console.log(`   任务数量: ${config.tasks.length} 个 ✅`);
        
        // 检查任务配置完整性
        let taskIssues = 0;
        for (const task of config.tasks) {
          if (!task.id || !task.name || !task.script || !task.schedule) {
            console.log(`   ❌ 任务 ${task.id || 'unknown'} 配置不完整`);
            taskIssues++;
          }
        }
        
        if (taskIssues > 0) {
          report.push(`发现 ${taskIssues} 个任务配置不完整`);
          issuesFound += taskIssues;
        }
      } else {
        console.log('   任务配置: ❌ (格式错误)');
        report.push('配置文件中 tasks 字段格式错误');
        issuesFound++;
      }
    } catch (error) {
      console.log(`   配置文件: ❌ (解析错误: ${error.message})`);
      report.push(`配置文件解析失败: ${error.message}`);
      issuesFound++;
    }
  } else {
    console.log('   配置文件: ❌ (不存在)');
    report.push('配置文件不存在');
    issuesFound++;
  }
  console.log('');

  // 4. 依赖检查
  console.log('📦 4. 依赖检查');
  console.log('-------------------');
  
  const packageJson = path.join(PROJECT_ROOT, 'package.json');
  if (fs.existsSync(packageJson)) {
    const pkg = JSON.parse(fs.readFileSync(packageJson, 'utf-8'));
    const dependencies = pkg.dependencies || {};
    
    console.log(`   总依赖数: ${Object.keys(dependencies).length}`);
    
    // 检查核心依赖
    const coreDeps = ['node-cron', 'zod'];
    for (const dep of coreDeps) {
      if (dependencies[dep]) {
        console.log(`   ${dep}: ✅ (已声明)`);
      } else {
        console.log(`   ${dep}: ❌ (缺失)`);
        report.push(`缺少核心依赖: ${dep}`);
        issuesFound++;
      }
    }
    
    // 检查 node_modules
    const nodeModulesPath = path.join(PROJECT_ROOT, 'node_modules');
    if (fs.existsSync(nodeModulesPath)) {
      console.log('   node_modules: ✅ (已安装)');
    } else {
      console.log('   node_modules: ⚠️  (未安装)');
      report.push('依赖未安装，需要运行 npm install');
    }
  } else {
    console.log('   package.json: ❌ (不存在)');
    report.push('package.json 不存在');
    issuesFound++;
  }
  console.log('');

  // 5. 任务脚本检查
  console.log('📝 5. 任务脚本检查');
  console.log('-------------------');
  
  const tasksDir = path.join(PROJECT_ROOT, 'tasks');
  if (fs.existsSync(tasksDir)) {
    const taskFiles = fs.readdirSync(tasksDir).filter(f => f.endsWith('.js'));
    console.log(`   任务脚本: ${taskFiles.length} 个 ✅`);
    
    // 检查配置中的任务脚本是否存在
    const configPath = path.join(PROJECT_ROOT, 'opencron-config.json');
    if (fs.existsSync(configPath)) {
      const config = JSON.parse(fs.readFileSync(configPath, 'utf-8'));
      for (const task of config.tasks) {
        const taskScript = path.join(PROJECT_ROOT, task.script);
        if (fs.existsSync(taskScript)) {
          console.log(`   - ${task.id}: ✅ (存在)`);
        } else {
          console.log(`   - ${task.id}: ❌ (脚本不存在: ${task.script})`);
          report.push(`任务 ${task.id} 的脚本不存在: ${task.script}`);
          issuesFound++;
        }
      }
    }
  } else {
    console.log('   任务目录: ❌ (不存在)');
    report.push('任务脚本目录不存在');
    issuesFound++;
  }
  console.log('');

  // 6. 系统资源检查
  console.log('📊 6. 系统资源检查');
  console.log('-------------------');
  
  const totalMem = os.totalmem();
  const freeMem = os.freemem();
  const memUsagePercent = ((totalMem - freeMem) / totalMem) * 100;
  
  console.log(`   内存使用: ${(memUsagePercent).toFixed(1)}% (${(freeMem/(1024*1024*1024)).toFixed(2)}GB 可用)`);
  
  if (memUsagePercent > 80) {
    console.log('   ⚠️  内存使用率过高');
    report.push(`内存使用率过高: ${memUsagePercent.toFixed(1)}%`);
  } else {
    console.log('   ✅ 内存使用正常');
  }
  
  console.log(`   CPU 核心数: ${os.cpus().length}`);
  console.log(`   系统平台: ${os.platform()}-${os.arch()}`);
  console.log('');

  // 7. 权限检查
  console.log('🔒 7. 权限检查');
  console.log('-------------------');
  
  try {
    // 检查项目目录写入权限
    const testFile = path.join(PROJECT_ROOT, '.health-check-test');
    fs.writeFileSync(testFile, 'test');
    fs.unlinkSync(testFile);
    console.log('   项目目录写入权限: ✅');
  } catch (error) {
    console.log(`   项目目录写入权限: ❌ (${error.message})`);
    report.push(`项目目录无写入权限: ${error.message}`);
    issuesFound++;
  }
  
  // 检查日志目录权限
  const logsDir = path.join(PROJECT_ROOT, 'logs');
  if (fs.existsSync(logsDir)) {
    try {
      const logTestFile = path.join(logsDir, '.permission-test');
      fs.writeFileSync(logTestFile, 'test');
      fs.unlinkSync(logTestFile);
      console.log('   日志目录写入权限: ✅');
    } catch (error) {
      console.log(`   日志目录写入权限: ❌ (${error.message})`);
      report.push(`日志目录无写入权限: ${error.message}`);
      issuesFound++;
    }
  } else {
    console.log('   日志目录: ⚠️  (不存在，需要创建)');
  }
  console.log('');

  // 8. 最终报告
  console.log('📋 8. 最终报告');
  console.log('================');
  console.log(`   检查项目: 7 个类别`);
  console.log(`   发现问题: ${issuesFound} 个`);
  console.log('');
  
  if (issuesFound === 0) {
    console.log('🎉 恭喜！系统完全健康，可以正常使用！');
    console.log('');
    console.log('💡 建议操作：');
    console.log('   - 启动调度器: node src/scheduler.js');
    console.log('   - 查看任务: npx opencron list');
    console.log('   - 查看日志: npx opencron logs');
  } else {
    console.log('⚠️  发现问题，建议修复：');
    report.forEach((issue, index) => {
      console.log(`   ${index + 1}. ${issue}`);
    });
    console.log('');
    console.log('💡 修复建议：');
    console.log('   - 依赖问题: npm install');
    console.log('   - 配置问题: 检查 opencron-config.json');
    console.log('   - 权限问题: 检查目录权限');
    console.log('   - 文件缺失: 重新初始化项目');
  }
  
  console.log('');
  console.log('🏥 健康检查完成');

} catch (error) {
  console.error('❌ 健康检查失败：', error.message);
  process.exit(1);
}
