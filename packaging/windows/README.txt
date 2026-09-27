NewspaperAuto · Windows 使用说明
=======================================

本压缩包是「解压即用」的 Windows 版本，内置 Python 运行时，
目标电脑无需安装 Python，也无需联网安装任何依赖（抓取时需能访问报社官网）。

一、快速开始
------------
1. 把整个文件夹解压到任意【可写】目录（例如 D:\NewspaperAuto）。
   请不要放在 C:\Program Files 下（无写权限会导致无法保存数据）。
2. 打开看板：双击 NewspaperAuto.exe（或双击 Start-Dashboard.cmd，两者等效）
   浏览器会自动打开 http://127.0.0.1:8788/ ，在页面里填写关键词并点「开始扫描」。

   ★ 看板在**后台运行**：关掉命令行窗口不会停止它。要停止请执行
       NewspaperAuto.exe dashboard --stop
     要看它是否在运行：
       NewspaperAuto.exe dashboard --status
3. 只扫一次：双击 Run-Scan.cmd
   按默认关键词扫描「今天 + 昨天」，结果显示在窗口中。

二、命令行用法（可选）
----------------------
在文件夹内打开 PowerShell / cmd，执行：

  NewspaperAuto.exe                            起看板（后台 + 自动开浏览器）
  NewspaperAuto.exe dashboard --stop           停止后台看板
  NewspaperAuto.exe dashboard --status         查看看板是否在运行
  NewspaperAuto.exe dashboard --scan           起看板并立即按上次设置扫描
  NewspaperAuto.exe --help                     查看帮助
  NewspaperAuto.exe --version                  查看版本
  NewspaperAuto.exe scan --days 7              回溯 7 天
  NewspaperAuto.exe scan --kw 人工智能 --kw 教育
  NewspaperAuto.exe scan --only 湛江日报,南方日报
  NewspaperAuto.exe scan --since 2026-09-20 --until 2026-09-25
  NewspaperAuto.exe scan --export-dir .\exports --export-format csv

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
- 页面提示「无法连接看板服务」：说明看板服务已经不在了（例如电脑重启后）。
  重新双击 NewspaperAuto.exe，再点页面里的「重新连接看板」即可。
  用 dashboard --status 可以确认服务是否在运行。
- 端口 8788 被占用：程序会**自动改用系统分配的空闲端口**并把地址打印出来；
  也可手动指定：NewspaperAuto.exe dashboard --port 9000 --open
- 首次运行 Windows 可能弹出 SmartScreen 提示：本 exe 未做代码签名，
  点「更多信息」→「仍要运行」即可。
- 中文乱码：脚本已设 UTF-8；若手动运行时仍乱码，先执行 chcp 65001。
- 卸载：若是用 setup.exe 安装的，从「设置 → 应用」卸载即可；
  **卸载不会删除 state\ 与 hits\ 里的扫描数据**，需要彻底清理请手动删除安装目录。
