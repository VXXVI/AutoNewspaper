NewspaperAuto · Windows 7 使用说明
==================================

本压缩包是「解压即用」的 Windows 7 版本，已内置 Python 3.8.10 运行时，
目标电脑无需安装 Python，也无需联网安装任何依赖（扫描时需能访问报社官网）。

包里只有一个程序：NewspaperAuto.exe
------------------------------------
双击它就行：会在后台起看板，并自动打开浏览器。**不会弹出命令行黑窗口。**

★ 运行前请确认（Windows 7 的硬性要求）
-------------------------------------
1) Windows 7 **SP1**（Service Pack 1）；
2) **KB2533623**；
3) 「更新通用 C 运行时（UCRT）」**KB2999226**，或 VC++ 2015-2022 运行库(x64)。

这三项通常随 Windows Update 装好。如果双击后报「无法启动此程序，因为计算机中丢失
api-ms-win-crt-*.dll / ucrtbase.dll」，就是缺第 3 项：到微软官网搜 KB2999226 装上。

一、快速开始
------------
1. 把整个文件夹解压到任意【可写】目录（例如 D:\NewspaperAuto）。
   请不要放在 C:\Program Files 下（无写权限会导致无法保存数据）。
2. 双击 NewspaperAuto.exe
   浏览器会自动打开 http://127.0.0.1:8788/ ，在页面里填关键词、点「开始扫描」。
3. 看板在**后台运行**：没有窗口需要留着，也不用管它。
   - 要再打开页面：再双击一次 NewspaperAuto.exe（已在运行会直接复用）。
   - 要停止它：见下面「三、命令行」里的 dashboard --stop。

二、数据位置
------------
state\（增量状态）、hits\（报告与历史命中）、logs\（运行日志）都在 exe 同目录，
papers.custom.json（自定义报社）同样在 exe 同目录。整个文件夹可整体拷贝换机。

三、命令行（可选，一般用户用不到）
----------------------------------
在本文件夹内打开 PowerShell / cmd，执行：

  NewspaperAuto.exe dashboard --stop       停止后台看板
  NewspaperAuto.exe dashboard --status     查看看板是否在运行
  NewspaperAuto.exe scan --days 7          回溯 7 天
  NewspaperAuto.exe scan --kw 人工智能 --kw 教育
  NewspaperAuto.exe scan --only 湛江日报,南方日报
  NewspaperAuto.exe scan --since 2026-09-20 --until 2026-09-25
  NewspaperAuto.exe --help                 查看全部用法

★ 一个必须知道的限制：本 exe 是**没有控制台窗口**的 GUI 程序，Windows 的 shell
  不会等它跑完（提示符会立刻回来），`>` 重定向也拿不到内容。所以：

  - 每次 scan 都会把输出**同时**写进 logs\scan-YYYY-MM-DD.log，想看结果就看它；
  - 想在终端里等它跑完，请写成：  start /wait "" NewspaperAuto.exe scan --days 2

四、每天自动扫描（任务计划程序）
--------------------------------
「任务计划程序」→ 创建基本任务 → 每天 → 启动程序：
  程序或脚本: D:\NewspaperAuto\NewspaperAuto.exe
  添加参数:   scan --days 2 --no-state
起始于那一步不用填。**不需要任何 .cmd 包装**：它本来就是无窗口程序，不会闪黑窗，
扫描日志会自己落到 logs\scan-YYYY-MM-DD.log。

五、常见问题
------------
- 页面提示「无法连接看板服务」：看板服务已经不在了（例如电脑重启后）。
  重新双击 NewspaperAuto.exe 即可。
- 端口 8788 被占用：程序会自动改用系统分配的空闲端口并把地址写入
  state\dashboard.json；也可手动指定 NewspaperAuto.exe dashboard --port 9000 --open。
- 首次运行 Windows 可能弹 SmartScreen 提示：本 exe 未做代码签名，
  点「更多信息」→「仍要运行」即可。
- 想确认能不能跑起来：双击后浏览器若能打开看板，就说明运行时没问题。

六、为什么是 Python 3.8
-----------------------
Python 官方从 3.9 起不再支持 Windows 7；3.8 是最后一个支持 Win7 的版本。
本包的 NewspaperAuto.exe 因此用 Python 3.8.10 构建（主线的 windows-x64 版本用
Python 3.12，只能在 Windows 10 1803+ 上运行，请不要在 Win7 上使用）。
