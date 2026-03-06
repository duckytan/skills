#!/usr/bin/env node
/**
 * opencron 快速安装脚本
 * 
 * 智能选择安装方式：
 * 1. 尝试全局安装（默认）
 * 2. 权限问题 → 提供解决方案
 * 3. 最后手段 → 局部安装
 */

import { execSync } from 'child_process';
import path from 'path';
import fs from 'fs';
import { homedir } from 'os';
import { saveOpencronPath } from './config.js';

const PATH_CONFIG_FILE = path.join(homedir(), '.opencron-path.json');

console.log('\n🚀 opencron-system 安装程序 v2.1.0\n');
console.log('='.repeat(50) + '\n');

// 策略 1: 尝试全局安装
console.log('📦 策略 1: 全局安装（推荐）\n');
console.log('执行：npm install -g opencron-system\n');

try {
  execSync('npm install -g opencron-system', { 
    stdio: 'inherit',
    env: { ...process.env }
  });
  
  console.log('\n✅ 安装成功！\n');
  
  // 保存安装路径
  try {
    const npmRoot = execSync('npm root -g', { encoding: 'utf-8' }).trim();
    const opencronRoot = path.join(npmRoot, 'opencron-system');
    saveOpencronPath(opencronRoot, 'global', '2.1.0');
    console.log(`💾 已保存路径到：${PATH_CONFIG_FILE}\n`);
  } catch (err) {
    console.log('⚠️  保存路径失败，下次将重新查找\n');
  }
  
  // 验证
  console.log('🔍 验证安装...\n');
  execSync('opencron --version', { stdio: 'inherit' });
  
  console.log('\n🎉 安装完成！opencron-system 已就绪\n');
  console.log('💡 使用方式：');
  console.log('   opencron --version    # 查看版本');
  console.log('   opencron init         # 初始化');
  console.log('   opencron start        # 启动调度器\n');
  
  process.exit(0);
  
} catch (error) {
  console.log('\n⚠️  全局安装失败！\n');
  
  // 分析错误
  const errorMsg = error.message || '';
  
  if (errorMsg.includes('EACCES') || errorMsg.includes('permission denied')) {
    console.log('❌ 原因：权限不足\n');
    
    console.log('💡 解决方案（按推荐顺序）：\n');
    
    console.log('方案 1: 以管理员身份运行');
    console.log('   - Windows: 右键 → 以管理员身份运行');
    console.log('   - Linux/macOS: sudo npm install -g opencron-system\n');
    
    console.log('方案 2: 修复 npm 权限（Linux/macOS 推荐）');
    console.log('   mkdir ~/.npm-global');
    console.log('   npm config set prefix \'~/.npm-global\'');
    console.log('   echo \'export PATH=~/.npm-global/bin:$PATH\' >> ~/.bashrc');
    console.log('   source ~/.bashrc\n');
    
    console.log('方案 3: 使用局部安装（最后手段）\n');
    
  } else {
    console.log('❌ 原因：未知错误\n');
    console.log(errorMsg + '\n');
  }
  
  // 询问用户是否使用局部安装
  console.log('🤔 是否使用局部安装？（安装在当前项目）\n');
  console.log('   回答 "是" 或按回车键继续\n');
  
  // 简单的 readline 提示
  process.stdout.write('输入：');
  process.stdin.resume();
  process.stdin.setEncoding('utf8');
  
  process.stdin.on('data', (input) => {
    process.stdin.pause();
    
    const answer = input.trim().toLowerCase();
    
    if (answer === '' || answer === '是' || answer === 'yes' || answer === 'y') {
      console.log('\n📦 策略 2: 局部安装\n');
      
      const projectRoot = process.cwd();
      console.log(`安装位置：${projectRoot}\n`);
      
      try {
        execSync('npm install opencron-system', { 
          cwd: projectRoot,
          stdio: 'inherit'
        });
        
        console.log('\n✅ 局部安装成功！\n');
        
        // 保存安装路径
        const localRoot = path.join(projectRoot, 'node_modules', 'opencron-system');
        saveOpencronPath(localRoot, 'local', '2.1.0');
        console.log(`💾 已保存路径到：${PATH_CONFIG_FILE}\n`);
        
        console.log('🔍 验证安装...\n');
        execSync('npx opencron --version', { 
          cwd: projectRoot,
          stdio: 'inherit'
        });
        
        console.log('\n🎉 安装完成！\n');
        console.log('💡 使用方式：');
        console.log('   npx opencron --version    # 查看版本');
        console.log('   npx opencron init         # 初始化');
        console.log('   npx opencron start        # 启动调度器\n');
        console.log('⚠️  注意：只能在项目目录内使用 npx 命令\n');
        
      } catch (err) {
        console.log('\n❌ 局部安装失败！\n');
        console.log(err.message + '\n');
        console.log('💡 请手动安装或联系管理员\n');
        process.exit(1);
      }
      
      process.exit(0);
    } else {
      console.log('\n❌ 安装已取消\n');
      console.log('💡 可以手动运行：');
      console.log('   npm install -g opencron-system  # 全局安装');
      console.log('   npm install opencron-system     # 局部安装\n');
      process.exit(0);
    }
  });
  
}
