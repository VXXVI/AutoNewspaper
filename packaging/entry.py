"""PyInstaller 单文件可执行入口。

只做一件事：把打包后的进程交给 :func:`newspaper_auto.exe.main` 分发。
"""

from newspaper_auto.exe import main

if __name__ == "__main__":
    raise SystemExit(main())
