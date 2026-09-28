NewspaperAuto · Windows 7 便携版使用说明
========================================

这是什么
--------
「解压即用」的 Windows 7 版本：包内自带 Python 3.8.10 运行时（这是 Windows 7
能够运行的最后一个 Python 版本），目标电脑无需安装 Python，也不需要联网安装
任何依赖（扫描时需要能访问报社官网）。

本包**零第三方依赖**，只用 Python 标准库；整个文件夹可以整体拷到 U 盘或别的
电脑继续用。


重要：系统要求
--------------
本版针对 Windows 7 打包，运行时需要：

  1) Windows 7 **SP1**（Service Pack 1）；
  2) 已安装 **KB2533623**（Python 3.8 的硬性要求）；
  3) 已安装「更新通用 C 运行时（UCRT）」**KB2999226**，或 VC++ 2015-2022 运行库(x64)。

这三项通常随 Windows Update 一并装上。如果双击后报「无法启动此程序，因为计算机中
丢失 api-ms-win-crt-*.dll / ucrtbase.dll」，就是缺第 3 项：到微软官网搜索安装
「Update for Universal C Runtime in Windows (KB2999226)」即可。

（本包已自带 vcruntime140.dll，通常无需再单独安装 VC++ 运行库。）


一、快速开始（不会有黑窗口弹出来）
----------------------------------
1. 把整个文件夹解压到任意【可写】目录（例如 D:\NewspaperAuto）。
   不要放在 C:\Program Files 下（那里无写权限，会导致无法保存数据）。

2. 双击 **start-dashboard.vbs**
   只会在浏览器里打开看板（默认 http://127.0.0.1:8788/），
   **不会弹出任何命令行黑窗口**。在页面里填关键词、选日期范围，点「开始扫描」。

   - 看板在后台运行：关掉浏览器不会停止它。
   - 想再打开页面：再双击一次 start-dashboard.vbs（已在运行会直接复用）。
   - 想停止看板：双击 stop-dashboard.cmd（这一步会显示一个窗口告诉你结果，
     因为「停了吗」需要给你反馈；它停完就关，不会常驻）。

3. 想手动扫一次并当场看结果：双击 **scan-now.cmd**（会显示扫描进度与命中）。

> 三个启动脚本的分工：
>   start-dashboard.vbs   无窗口，打开看板（日常就用它）
>   start-dashboard.cmd   同上，但保留命令行窗口（排查问题时用）
>   scan-now.cmd          手动扫描一次，窗口里能看到进度与结果
>   stop-dashboard.cmd    停止后台看板


二、每天自动扫描（任务计划程序，同样不弹窗）
--------------------------------------------
「任务计划程序」→ 创建基本任务 → 触发器选「每天」→ 操作选「启动程序」：

    程序或脚本: D:\NewspaperAuto\python\pythonw.exe
    添加参数:   "D:\NewspaperAuto\run.py" scan
    起始于:     D:\NewspaperAuto

要点：
- 一定要用 **pythonw.exe**（不是 python.exe）—— 这样每天触发时**不会闪出黑窗口**；
  输出会自己写进 logs\scan-YYYY-MM-DD.log。
- 参数里的 **scan** 让程序扫描「今天 + 昨天」并只报「新增」；命中会记进
  hits\，看板的历史与趋势图就有数据。
- 如果你同时用 GitHub Actions 跑同一批关键词，请把参数改成 `scan --no-state`，
  这样本机任务只写日志、不碰 state\ 与 hits\，两边不会互相覆盖。
- 报纸一般凌晨上线，建议时间设在 07:00 之后；机器需开机并处于登录状态。


三、数据位置
------------
以下目录都在本文件夹内，程序会按需自动创建：

    state\  增量状态（记录已判定过的文章，保证只报新增）
    hits\   报告与历史命中（hits\index.html 可直接浏览）
    logs\   运行日志（logs\scan-YYYY-MM-DD.log、logs\dashboard.log）
    papers.custom.json   自定义报社配置

整个文件夹可整体拷贝换机。


四、命令行（可选，一般用户用不到）
----------------------------------
在本文件夹内打开命令提示符 / PowerShell，执行（run.py 的子命令与官方 exe 一致）：

    python\python.exe run.py scan                        扫描一次（今天+昨天）
    python\python.exe run.py scan --days 7               回溯最近 7 天
    python\python.exe run.py scan --kw 人工智能 --kw 教育
    python\python.exe run.py scan --only 湛江日报,南方日报
    python\python.exe run.py scan --since 2026-09-20 --until 2026-09-25
    python\python.exe run.py scan --dry                  只探测入口与版面，不抓正文
    python\python.exe run.py scan --export-dir .\exports --export-format csv

    python\python.exe run.py dashboard --status          看板是否在运行
    python\python.exe run.py dashboard --stop            停止后台看板
    python\python.exe run.py --help                      查看全部用法

更完整的参数见 README.md 与 docs\ 目录。


五、常见问题
------------
Q: 双击 start-dashboard.vbs 没有任何反应？
A: 少数系统禁用了 Windows Script Host（.vbs）。改用 start-dashboard.cmd
   （会显示一个命令行窗口，功能完全一样）。也可以在本文件夹打开命令提示符执行：
       python\python.exe run.py

Q: 页面提示「无法连接看板服务」？
A: 看板服务已经不在了（例如电脑重启后）。重新双击 start-dashboard.vbs 即可。

Q: 窗口里中文显示为乱码？
A: 启动脚本已自动执行 chcp 65001。若仍乱码，把命令行窗口字体换成
   「新宋体」或「Consolas」再试。

Q: 端口 8788 被占用？
A: 程序会自动改用系统分配的空闲端口，并把实际地址写进 state\dashboard.json。

Q: 首次运行弹出 SmartScreen 提示？
A: 本包未做代码签名，点「更多信息」→「仍要运行」即可。

Q: 提示找不到 curl？
A: Windows 7 不带系统 curl（curl.exe 是 Windows 10 1803 才自带的）。本工具默认
   用 Python 标准库抓取，curl 只是可选的兜底，没有它不影响使用。

Q: 想确认自带的 Python 能不能在这台机器上跑？
A: 在本文件夹打开命令提示符，执行：
       python\python.exe -c "import ssl; print(ssl.OPENSSL_VERSION)"
   能打印出 OpenSSL 版本号，就说明运行时可用、HTTPS 抓取没问题。


六、为什么是 Python 3.8
-----------------------
Python 3.9 起官方已不再支持 Windows 7；3.8.10 是最后一个提供 Windows 二进制
安装包的版本。本包因此固定在 3.8.10，并把项目源码放在 src\ 下直接运行
（不经过 pip 安装）。项目代码已确认与 Python 3.8 兼容。
