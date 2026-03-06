#!/usr/bin/env node
/**
 * opencron 路径查找模块
 * 
 * 智能查找 opencron 安装位置，支持多种安装场景
 */

import { existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

/**
 * 智能查找 opencron 根目录
 * 
 * 查找策略（优先级从高到低）：
 * 1. 环境变量 OPENCRON_ROOT
 * 2. skill 目录的相对路径（当前工作空间）
 * 3. 父目录查找 opencron-config.json
 * 4. npm 全局安装位置
 * 5. 默认路径
 * 
 * @returns {string} opencron 根目录路径
 */
export function findOpencronRoot() {
  // 策略 1: 环境变量（最高优先级）
  if (process.env.OPENCRON_ROOT) {
    console.log(`[DEBUG] 使用环境变量 OPENCRON_ROOT: ${process.env.OPENCRON_ROOT}`);
    return process.env.OPENCRON_ROOT;
  }

  // 策略 2: skill 目录的相对路径（当前工作空间）
  // 从 .opencode/skills/opencron/scripts/utils/ 往上找 project/opencron
  // 可能的位置：
  //   - D:\AI-Project\AI-AgentWorkSpace-Ducky\project\opencron (项目根目录的 project/opencron)
  //   - D:\AI-Project\AI-AgentWorkSpace-Ducky\.opencode\skills\project\opencron (skill 自带)
  
  const checkPaths = [
    join(__dirname, '..', '..', '..', '..', '..', 'project', 'opencron'),  // 上 5 级到项目根，再进 project/opencron
    join(__dirname, '..', '..', '..', '..', 'project', 'opencron'),        // 上 4 级到.opencode，再进 project/opencron
  ];
  
  for (const checkPath of checkPaths) {
    const configFile = join(checkPath, 'opencron-config.json');
    if (existsSync(checkPath) && existsSync(configFile)) {
      console.log(`[DEBUG] 使用相对路径：${checkPath}`);
      return checkPath;
    }
  }

  // 策略 3: 往上查找 opencron-config.json
  let currentDir = __dirname;
  const maxDepth = 10;
  for (let i = 0; i < maxDepth; i++) {
    const configFile = join(currentDir, 'opencron-config.json');
    if (existsSync(configFile)) {
      console.log(`[DEBUG] 查找配置文件到：${currentDir}`);
      return currentDir;
    }
    const parentDir = dirname(currentDir);
    if (parentDir === currentDir) break; // 到达根目录
    currentDir = parentDir;
  }

  // 策略 4: 检查 npm 全局安装
  try {
    const { execSync } = require('child_process');
    const npmRoot = execSync('npm root -g', { encoding: 'utf-8' }).trim();
    const globalOpencron = join(npmRoot, 'opencron-system');
    if (existsSync(join(globalOpencron, 'opencron-config.json'))) {
      console.log(`[DEBUG] 使用全局安装：${globalOpencron}`);
      return globalOpencron;
    }
  } catch (error) {
    // 忽略错误
  }

  // 策略 5: 默认路径（最后手段）
  // 默认是第一个检查路径
  const defaultPath = checkPaths[0];
  console.warn(`[WARN] 使用默认路径：${defaultPath}`);
  return defaultPath;
}

/**
 * 查找配置文件
 * @param {string} opencronRoot - opencron 根目录
 * @returns {string} 配置文件路径
 */
export function findConfigFile(opencronRoot) {
  return join(opencronRoot, 'opencron-config.json');
}

/**
 * 查找任务脚本目录
 * @param {string} opencronRoot - opencron 根目录
 * @returns {string} 任务目录路径
 */
export function findTasksDir(opencronRoot) {
  return join(opencronRoot, 'tasks');
}

/**
 * 验证路径是否有效
 * @param {string} opencronRoot - opencron 根目录
 * @returns {Object} 验证结果
 */
export function validateOpencronRoot(opencronRoot) {
  const checks = {
    exists: existsSync(opencronRoot),
    hasConfig: existsSync(join(opencronRoot, 'opencron-config.json')),
    hasScheduler: existsSync(join(opencronRoot, 'src', 'scheduler.js')),
    hasTasks: existsSync(join(opencronRoot, 'tasks'))
  };

  return {
    valid: checks.exists && checks.hasConfig && checks.hasScheduler,
    checks,
    path: opencronRoot
  };
}

// 缓存查找结果
let cachedRoot = null;

/**
 * 获取 opencron 根目录（带缓存）
 * @returns {string} opencron 根目录
 */
export function getOpencronRoot() {
  if (!cachedRoot) {
    cachedRoot = findOpencronRoot();
  }
  return cachedRoot;
}

/**
 * 清除缓存（用于测试）
 */
export function clearCache() {
  cachedRoot = null;
}

// 如果直接运行，打印查找结果
if (process.argv[1] && process.argv[1].includes('path-resolver.js')) {
  const root = getOpencronRoot();
  const validation = validateOpencronRoot(root);
  
  console.log('\n🔍 opencron 路径查找结果：\n');
  console.log(`根目录：${root}`);
  console.log(`验证结果：${validation.valid ? '✅ 有效' : '❌ 无效'}\n`);
  
  console.log('检查项：');
  console.log(`  - 目录存在：${validation.checks.exists ? '✅' : '❌'}`);
  console.log(`  - 配置文件：${validation.checks.hasConfig ? '✅' : '❌'}`);
  console.log(`  - 调度器：${validation.checks.hasScheduler ? '✅' : '❌'}`);
  console.log(`  - 任务目录：${validation.checks.hasTasks ? '✅' : '❌'}\n`);
  
  if (!validation.valid) {
    console.log('💡 建议：');
    console.log('   1. 设置环境变量：export OPENCRON_ROOT=/path/to/opencron');
    console.log('   2. 确保 opencron-config.json 存在');
    console.log('   3. 检查目录结构是否正确\n');
  }
}
