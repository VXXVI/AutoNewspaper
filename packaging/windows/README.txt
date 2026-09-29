NewspaperAuto · Windows 使用说明
=======================================

本压缩包是「解压即用」的 Windows 版本，内置 Python 运行时，
目标电脑无需安装 Python，也无需联网安装任何依赖（抓取时需能访问报社官网）。

★ Windows 7 用户注意：本包在 Windows 10 1803+ 上开箱即用；在 Windows 7 上运行需要
  Windows 7 **SP1** + **KB2533623** + 通用 C 运行时（**KB2999226**，或 VC++ 2015-2022
  运行库 x64）。缺这些会报「计算机中丢失 api-ms-win-crt-*.dll / ucrtbase.dll」——
  它们通常随 Windows Update 装好；实在没有就到微软官网搜 KB2999226 装上即可。
  （Win7 请下载 `NewspaperAuto-*-win7-x64.zip`，那是用 Python 3.8 专门构建的版本。）

包里只有一个程序：NewspaperAuto.exe
------------------------------------
双击它就行：会在后台起看板，并自动打开浏览器。**不会弹出命令行黑窗口。**

一、快速开始
------------
1. 把整个文件夹解压到任意【可写】目录（例如 D:\NewspaperAuto）。
   请不要放在 C:\Program Files 下（无写权限会导致无法保存数据）。
2. 双击 NewspaperAuto.exe
   浏览器会自动打开 http://127.0.0.1:8788/ ，在页面里填关键词、点「开始扫描」。
3. 看板在**后台运行**：没有窗口需要留着，也不用管它。
   - 要再打开页面：再双击一次 NewspaperAuto.exe（已在运行会直接复用，不会重开一份）。
   - 要停止它：见下面「三、命令行」里的 dashboard --stop。

二、数据位置
------------
state\（增量状态）、hits\（报告与历史命中）、logs\（运行日志）都在本目录，
papers.custom.json（自定义报社）同样在本目录。整个文件夹可整体拷贝换机。

三、命令行（可选，一般用户用不到）
----------------------------------
在文件夹内打开 PowerShell / cmd，执行：

  NewspaperAuto.exe dashboard --stop       停止后台看板
  NewspaperAuto.exe dashboard --status     查看看板是否在运行
  NewspaperAuto.exe scan --days 7          回溯 7 天
  NewspaperAuto.exe scan --kw 人工智能 --kw 教育
  NewspaperAuto.exe scan --only 湛江日报,南方日报
  NewspaperAuto.exe scan --since 2026-09-20 --until 2026-09-25
  NewspaperAuto.exe scan --export-dir .\exports --export-format csv
  NewspaperAuto.exe --help                 查看全部用法

★ 一个必须知道的限制：本 exe 是**没有控制台窗口**的 GUI 程序，Windows 的 shell
  不会等它跑完（提示符会立刻回来），`>` 重定向也拿不到内容。所以：

  - 每次 scan 都会把输出**同时**写进 logs\scan-YYYY-MM-DD.log，想看结果就看它；
  - 想在终端里等它跑完，请写成：  start /wait "" NewspaperAuto.exe scan --days 2

为什么不做成「带控制台的命令行版」：Windows 的 exe 文件头只能二选一 ——
要么双击不弹窗（本版），要么终端里正常但双击必弹一个黑窗口。取舍是优先双击即用。

四、每天自动扫描（任务计划程序）
--------------------------------
「任务计划程序」→ 创建基本任务 → 每天 → 启动程序：
  程序或脚本: D:\NewspaperAuto\NewspaperAuto.exe
  添加参数:   scan --days 2 --no-state
起始于那一步不用填。**不需要任何 .cmd 包装**：它本来就是无窗口程序，不会闪黑窗，
扫描日志会自己落到 logs\scan-YYYY-MM-DD.log。

五、常见问题
------------
- 页面提示「无法连接看板服务」：说明看板服务已经不在了（例如电脑重启后）。
  重新双击 NewspaperAuto.exe，再点页面里的「重新连接看板」即可。
  dashboard --status 可以确认服务是否在运行。
- 端口 8788 被占用：程序会**自动改用系统分配的空闲端口**并把地址写入
  state\dashboard.json；也可手动指定：
  NewspaperAuto.exe dashboard --port 9000 --open
- 首次运行 Windows 可能弹 SmartScreen 提示：本 exe 未做代码签名，
  点「更多信息」→「仍要运行」即可。
- 启动后什么都没发生：看 logs\ 目录；另外双击失败时会弹一个原生错误对话框
  （报错内容 + 提示去哪里看日志）。
- 卸载：本包是**便携版**，没有安装过程也没写注册表，**直接删掉整个文件夹即可**。
  数据都在 state\、hits\、logs\ 里，删之前请自行备份；删掉就无法恢复。
