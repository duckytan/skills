#!/usr/bin/env node
/**
 * opencron 全局配置
 * 
 * 统一导出 opencron 路径和配置，所有脚本共享
 */

import { existsSync, readFileSync, writeFileSync } from 'fs';
import { join, dirname } from 'path';
import { homedir } from 'os';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

// 路径配置文件（用户主目录）
const PATH_CONFIG_FILE = join(homedir(), '.opencron-path.json');

/**
 * 读取已保存的安装路径
 */
function loadSavedPath() {
  try {
    if (existsSync(PATH_CONFIG_FILE)) {
      const config = JSON.parse(readFileSync(PATH_CONFIG_FILE, 'utf-8'));
      if (config.opencronRoot && existsSync(join(config.opencronRoot, 'opencron-config.json'))) {
        console.log(`[DEBUG] 使用已保存路径：${config.opencronRoot}`);
        return config.opencronRoot;
      }
    }
  } catch (error) {
    console.log('[DEBUG] 读取保存路径失败:', error.message);
  }
  return null;
}

/**
 * 保存安装路径
 */
export function saveOpencronPath(opencronRoot, installType = 'local', version = '2.1.0') {
  try {
    const config = {
      opencronRoot,
      installType,
      version,
      installedAt: new Date().toISOString()
    };
    writeFileSync(PATH_CONFIG_FILE, JSON.stringify(config, null, 2), 'utf-8');
    console.log(`[DEBUG] 已保存路径到：${PATH_CONFIG_FILE}`);
    return true;
  } catch (error) {
    console.log('[DEBUG] 保存路径失败:', error.message);
    return false;
  }
}

/**
 * 清除保存的路径（用于重新配置）
 */
export function clearSavedPath() {
  try {
    if (existsSync(PATH_CONFIG_FILE)) {
      // 使用 trast-mechanism 删除
      const { execSync } = require('child_process');
      execSync(`python "${join(__dirname, '..', '..', '..', 'trast-mechanism', 'scripts', 'safe_copy.py')}" "${PATH_CONFIG_FILE}"`);
      console.log(`[DEBUG] 已清除保存的路径`);
      return true;
    }
  } catch (error) {
    console.log('[DEBUG] 清除路径失败:', error.message);
  }
  return false;
}

/**
 * 智能查找 opencron 根目录（带缓存）
 */
let cachedRoot = null;

function findOpencronRoot() {
  // 策略 0: 使用已保存的路径（最高优先级，0ms）
  const savedPath = loadSavedPath();
  if (savedPath) {
    cachedRoot = savedPath;
    return cachedRoot;
  }

  // 策略 1: 环境变量
  if (process.env.OPENCRON_ROOT) {
    cachedRoot = process.env.OPENCRON_ROOT;
    // 保存路径
    saveOpencronPath(cachedRoot, 'env');
    return cachedRoot;
  }

  // 策略 2: 多路径查找（兼容不同安装位置）
  const checkPaths = [
    // 从 .opencode/skills/opencron/scripts/utils/ 查找
    join(__dirname, '..', '..', '..', '..', 'project', 'opencron'),  // 上 4 级 → project/opencron
    join(__dirname, '..', '..', '..', '..', '..', 'project', 'opencron'),  // 上 5 级 → 项目根 → project/opencron
    
    // 从 project/opencron/scripts/ 查找（直接安装）
    join(__dirname, '..', '..'),
    
    // npm 全局安装
    join(__dirname, '..', '..', '..')
  ];
  
  for (const checkPath of checkPaths) {
    if (existsSync(checkPath) && existsSync(join(checkPath, 'opencron-config.json'))) {
      cachedRoot = checkPath;
      // 保存路径，下次直接用
      saveOpencronPath(cachedRoot, 'local');
      return cachedRoot;
    }
  }
  
  // 默认返回第一个路径
  cachedRoot = checkPaths[0];
  return cachedRoot;
}

// 导出函数（每次调用返回缓存或查找结果）
export function getOpencronRoot() {
  return findOpencronRoot();
}

// 清除缓存（用于测试）
export function clearCache() {
  cachedRoot = null;
}

// 导出全局配置对象（惰性求值）
export const CONFIG_FILE = join(getOpencronRoot(), 'opencron-config.json');
export const TASKS_DIR = join(getOpencronRoot(), 'tasks');
export const LOGS_DIR = join(getOpencronRoot(), 'logs');
export const SRC_DIR = join(getOpencronRoot(), 'src');

// 导出验证函数
export function validate() {
  const root = getOpencronRoot();
  return {
    root,
    hasRoot: existsSync(root),
    hasConfig: existsSync(CONFIG_FILE),
    hasTasks: existsSync(TASKS_DIR),
    hasScheduler: existsSync(join(SRC_DIR, 'scheduler.js'))
  };
}

// 如果直接运行，打印配置
if (process.argv[1] && process.argv[1].includes('config.js')) {
  const validation = validate();
  console.log('\n📋 opencron 配置信息：\n');
  console.log(`根目录：${validation.root}`);
  console.log(`配置文件：${CONFIG_FILE}`);
  console.log(`验证结果：${validation.hasRoot && validation.hasConfig && validation.hasScheduler ? '✅ 有效' : '❌ 无效'}\n`);
  console.log('检查项:');
  console.log(`  - 根目录存在：${validation.hasRoot ? '✅' : '❌'}`);
  console.log(`  - 配置文件：${validation.hasConfig ? '✅' : '❌'}`);
  console.log(`  - 任务目录：${validation.hasTasks ? '✅' : '❌'}`);
  console.log(`  - 调度器：${validation.hasScheduler ? '✅' : '❌'}\n`);
  
  if (cachedRoot) {
    console.log('💡 缓存已启用（第二次调用直接使用缓存）\n');
  }
}
