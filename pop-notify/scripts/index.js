#!/usr/bin/env node

/**
 * Windows 弹窗通知 - pop-notify
 * 使用 PowerShell 调用 System.Windows.Forms.NotifyIcon
 * 零依赖，跨 Windows 版本
 */

const { execSync } = require('child_process');
const path = require('path');

// 解析命令行参数
function parseArgs(args) {
  const options = {
    title: '通知',
    message: '这是一条通知',
    icon: 'info',
    duration: 5000
  };

  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--title' && args[i + 1]) {
      options.title = args[i + 1];
      i++;
    } else if (args[i] === '--message' && args[i + 1]) {
      options.message = args[i + 1];
      i++;
    } else if (args[i] === '--icon' && args[i + 1]) {
      options.icon = args[i + 1];
      i++;
    } else if (args[i] === '--duration' && args[i + 1]) {
      options.duration = parseInt(args[i + 1], 10);
      i++;
    }
  }

  return options;
}

// 映射图标类型
function getIconType(iconName) {
  const iconMap = {
    'info': 'Info',
    'warning': 'Warning',
    'error': 'Error',
    'none': 'None'
  };
  return iconMap[iconName.toLowerCase()] || 'Info';
}

// 生成 PowerShell 命令
function generatePowerShellCommand(options) {
  const iconType = getIconType(options.icon);
  
  // 转义特殊字符
  const escapeString = (str) => {
    return str.replace(/'/g, "''");
  };

  const title = escapeString(options.title);
  const message = escapeString(options.message);
  
  // 映射到 .NET 图标名称
  const systemIconMap = {
    'Info': 'Information',
    'Warning': 'Warning',
    'Error': 'Error',
    'None': 'Application'
  };
  const systemIcon = systemIconMap[iconType];

  return `
Add-Type -AssemblyName System.Windows.Forms;
Add-Type -AssemblyName System.Drawing;
$n = New-Object System.Windows.Forms.NotifyIcon;
$n.Icon = [System.Drawing.SystemIcons]::${systemIcon};
$n.Visible = $true;
$n.BalloonTipIcon = '${iconType}';
$n.BalloonTipTitle = '${title}';
$n.BalloonTipText = '${message}';
$n.ShowBalloonTip(${options.duration});
Start-Sleep -Milliseconds ${options.duration};
$n.Dispose();
`.trim().replace(/\n/g, ' ');
}

// PowerShell 图标映射
const SystemIconMap = {
  'Info': 'Information',
  'Warning': 'Warning',
  'Error': 'Error',
  'None': 'Application'
};

// 主函数
function main() {
  const args = process.argv.slice(2);
  const options = parseArgs(args);

  try {
    const psCommand = generatePowerShellCommand(options);
    
    execSync(`powershell -ExecutionPolicy Bypass -Command "${psCommand}"`, {
      stdio: 'inherit',
      windowsHide: true
    });

    console.log('✅ 通知发送成功');
    process.exit(0);
  } catch (error) {
    console.error('❌ 通知发送失败:', error.message);
    process.exit(1);
  }
}

// 运行
main();
