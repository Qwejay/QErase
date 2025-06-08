# QErase - 文件粉碎工具

QErase 是一个轻量级的跨平台文件粉碎工具，提供安全的文件删除功能，防止文件被恢复。

## 功能特点

- 支持多种安全删除标准：
  - 美国国防部标准（DoD 5220.22-M）
  - 德国联邦信息安全办公室标准（VSITR）
  - 简单覆盖
- 简洁的用户界面
- 实时进度显示
- 跨平台支持（Windows、macOS、Linux）

## 安装要求

- Python 3.8 或更高版本
- PyQt6
- cryptography

## 安装步骤

1. 克隆仓库：
```bash
git clone https://github.com/yourusername/QErase.git
cd QErase
```

2. 安装依赖：
```bash
pip install -r requirements.txt
```

## 使用方法

运行程序：
```bash
python main.py
```

1. 点击"选择文件"按钮选择要删除的文件
2. 从下拉菜单中选择删除标准
3. 点击"开始粉碎"按钮开始安全删除过程

## 安全说明

- 文件删除后无法恢复
- 建议在删除重要文件前进行备份
- 程序使用标准的安全删除算法，确保文件数据被完全覆盖

## 许可证

MIT License 