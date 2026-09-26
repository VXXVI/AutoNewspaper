NewspaperAuto · Windows 便携版使用说明
=======================================

本压缩包是「解压即用」的 Windows 版本，内置 Python 运行时，
目标电脑无需安装 Python，也无需联网安装任何依赖（抓取时需能访问报社官网）。

一、快速开始
------------
1. 把整个文件夹解压到任意【可写】目录（例如 D:\NewspaperAuto）。
   请不要放在 C:\Program Files 下（无写权限会导致无法保存数据）。
2. 打开看板：双击 Start-Dashboard.cmd
   浏览器会自动打开 http://127.0.0.1:8788/ ，在页面里填写关键词并点「开始扫描」。
3. 只扫一次：双击 Run-Scan.cmd
   按默认关键词扫描「今天 + 昨天」，结果显示在窗口中。

二、命令行用法（可选）
----------------------
在文件夹内打开 PowerShell / cmd，执行：

  NewspaperAuto.exe --help                     查看帮助
  NewspaperAuto.exe --version                  查看版本
  NewspaperAuto.exe scan --days 7              回溯 7 天
  NewspaperAuto.exe scan --kw 人工智能 --kw 教育
  NewspaperAuto.exe scan --only 湛江日报,南方日报
  NewspaperAuto.exe scan --since 2026-09-20 --until 2026-09-25
  NewspaperAuto.exe scan --export-dir .\exports --export-format csv
  NewspaperAuto.exe dashboard --port 9000 --open

scan / dashboard 之后的参数与源码版的 daily_scan.py / dashboard.py 完全一致。

三、数据位置
------------
state\（增量状态）与 hits\（报告与历史命中）默认保存在本 exe 所在目录，
papers.custom.json（自定义报社）同样在本目录。整个文件夹可整体拷贝换机。

也可用环境变量或参数指定别处：

  set NEWSPAPER_DATA_DIR=D:\npa-data
  NewspaperAuto.exe scan --data-dir D:\npa-data

四、常见问题
------------
- 首次运行 Windows 可能弹出 SmartScreen 提示：本 exe 未做代码签名，
  点「更多信息」→「仍要运行」即可。
- 中文乱码：脚本已设 UTF-8；若手动运行时仍乱码，先执行 chcp 65001。
- 看板打不开：端口 8788 可能被占用，用 --port 指定其它端口，或
  NewspaperAuto.exe dashboard --port 0 --open 让系统自动分配。
