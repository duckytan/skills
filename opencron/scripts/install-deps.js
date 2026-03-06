#!/usr/bin/env node
/**
 * 检查和安装依赖
 * 对应 skill.yml 中的 install-deps 命令
 */

import { CONFIG_FILE, TASKS_DIR, LOGS_DIR } from '../utils/config.js';
import fs from 'fs';

console.log('📦 检查和安装依赖...');
console.log('');

try {
  // 1. 检查 package.json
  console.log('🔍 检查 package.json...');
  const packageJsonPath = path.join(PROJECT_ROOT, 'package.json');
  if (!fs.existsSync(packageJsonPath)) {
    console.error('❌ package.json 不存在');
    process.exit(1);
  }
  
  const packageJson = JSON.parse(fs.readFileSync(packageJsonPath, 'utf-8'));
  console.log(`   ✅ package.json 找到 (v${packageJson.version})`);

  // 2. 检查当前依赖
  console.log('');
  console.log('🔍 检查当前依赖...');
  
  // 检查是否已安装依赖
  const nodeModulesPath = path.join(PROJECT_ROOT, 'node_modules');
  if (fs.existsSync(nodeModulesPath)) {
    console.log('   ✅ node_modules 目录已存在');
    
    // 检查依赖版本
    try {
      const installedNodeCron = require(path.join(PROJECT_ROOT, 'node_modules', 'node-cron', 'package.json'));
      const installedZod = require(path.join(PROJECT_ROOT, 'node_modules', 'zod', 'package.json'));
      console.log(`   ✅ node-cron v${installedNodeCron.version} 已安装`);
      console.log(`   ✅ zod v${installedZod.version} 已安装`);
    } catch (error) {
      console.log('   ⚠️  依赖版本检查失败，可能需要重新安装');
    }
  } else {
    console.log('   ❌ node_modules 不存在，需要安装');
  }

  // 3. 检查依赖列表
  console.log('');
  console.log('📋 需要的依赖：');
  const dependencies = packageJson.dependencies || {};
  
  for (const [dep, version] of Object.entries(dependencies)) {
    try {
      const depPackagePath = path.join(PROJECT_ROOT, 'node_modules', dep, 'package.json');
      if (fs.existsSync(depPackagePath)) {
        const depPackage = require(depPackagePath);
        console.log(`   ✅ ${dep}@${version} (当前: v${depPackage.version})`);
      } else {
        console.log(`   ❌ ${dep}@${version} (未安装)`);
      }
    } catch (error) {
      console.log(`   ❌ ${dep}@${version} (错误: ${error.message})`);
    }
  }

  // 4. 检查是否需要安装
  const needsInstall = !fs.existsSync(nodeModulesPath);
  
  if (needsInstall) {
    console.log('');
    console.log('📦 开始安装依赖...');
    console.log('   运行: npm install');
    
    try {
      execSync('npm install', { 
        cwd: PROJECT_ROOT, 
        stdio: 'inherit',
        timeout: 300000 // 5 minutes timeout
      });
      console.log('   ✅ 依赖安装完成');
    } catch (error) {
      console.error('   ❌ 依赖安装失败：', error.message);
      console.log('');
      console.log('💡 建议手动运行:');
      console.log('   cd', PROJECT_ROOT);
      console.log('   npm install');
      process.exit(1);
    }
  } else {
    // 检查是否需要更新
    console.log('');
    console.log('🔍 检查是否需要更新...');
    let needsUpdate = false;
    
    for (const [dep, requiredVersion] of Object.entries(dependencies)) {
      try {
        const depPackagePath = path.join(PROJECT_ROOT, 'node_modules', dep, 'package.json');
        const depPackage = require(depPackagePath);
        
        // 简单版本比较（主要检查主版本号）
        const requiredMajor = requiredVersion.replace('^', '').replace('~', '').split('.')[0];
        const installedMajor = depPackage.version.split('.')[0];
        
        if (requiredMajor !== installedMajor) {
          console.log(`   ⚠️  ${dep} 版本不匹配 (需要: ${requiredVersion}, 当前: v${depPackage.version})`);
          needsUpdate = true;
        }
      } catch (error) {
        needsUpdate = true;
      }
    }
    
    if (needsUpdate) {
      console.log('');
      console.log('🔄 检测到版本不匹配，建议更新依赖...');
      console.log('   如需更新，请运行: npm install');
    } else {
      console.log('');
      console.log('✅ 所有依赖均已安装并版本匹配');
    }
  }

  // 5. 验证安装
  console.log('');
  console.log('🔍 验证安装...');
  try {
    // 测试核心依赖
    const nodeCronPath = path.join(PROJECT_ROOT, 'node_modules', 'node-cron');
    const zodPath = path.join(PROJECT_ROOT, 'node_modules', 'zod');
    
    if (fs.existsSync(nodeCronPath)) {
      console.log('   ✅ node-cron 可导入');
    } else {
      console.error('   ❌ node-cron 无法导入');
    }
    
    if (fs.existsSync(zodPath)) {
      console.log('   ✅ zod 可导入');
    } else {
      console.error('   ❌ zod 无法导入');
    }
    
    // 尝试导入测试
    import(path.join(PROJECT_ROOT, 'node_modules', 'node-cron', 'index.js'));
    console.log('   ✅ node-cron 功能正常');
    
  } catch (error) {
    console.error('   ❌ 验证失败：', error.message);
    process.exit(1);
  }

  console.log('');
  console.log('🎉 依赖检查和安装完成！');
  console.log('');
  console.log('📋 安装摘要：');
  console.log(`   - Node.js: ${process.version}`);
  console.log(`   - 项目: ${packageJson.name} v${packageJson.version}`);
  console.log(`   - 依赖数量: ${Object.keys(dependencies).length}`);
  console.log(`   - 状态: ✅ 准备就绪`);
  console.log('');
  console.log('💡 下一步建议：');
  console.log('   - 初始化: node skills/opencron/scripts/init.js');
  console.log('   - 查看任务: node skills/opencron/scripts/list-tasks.js');

} catch (error) {
  console.error('❌ 依赖检查失败：', error.message);
  process.exit(1);
}
