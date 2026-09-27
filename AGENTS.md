# AGENTS.md — AutoNewspaper 协作约定

> 本文件给 AI agent 读。目标：让 git 操作可预测，别再出现「碎提交刷屏」「两方抢推 main」「推失败被读成成功」。

## 1. 先确认落点，再动手

- 本仓库：`/Users/quark/Codex/Project/NewsPaperAuto`
- 唯一远端：`origin = https://github.com/VXXVI/AutoNewspaper.git`（private）
- 唯一主干：`main`。单人项目，**直接推 main，不要建长期分支、不要建 PR**。

⚠️ 本机还有别的仓库（`ListSkills`、`math-modeling-2026`、`atlas`）。**跑任何 git 命令前先确认落点**：

```bash
git rev-parse --show-toplevel    # 必须是 /Users/quark/Codex/Project/NewsPaperAuto
```

在 `~/Codex/Essay`、`~/Codex/VPS`、`~/Codex/Headache` 这类**没有自己 `.git`** 的目录里跑 git，
会静默落到**家目录仓库**（远端 `ListSkills`，跟踪了整个 `Codex/`）。这不是本仓库，别在这里提交任何东西。

## 2. CI 不再写入 main；运行数据在 `data` 分支

`.github/workflows/scan.yml` 每天 10:00（北京）在 CI 里执行扫描，但结果**不再提交回
`main`**：`main` 只承载源码与配置，`hits/`、`state/` 已在 `.gitignore` 中忽略，CI 会把它们
推送到专用的 **`data` 分支**（推送带 rebase 重试），并在下次运行前从该分支恢复。

因此：

1. **正常情况 main 只由你推进**，但仍建议推之前 `git fetch origin main` 对齐；
2. **推之后必须复核**（不能只看命令回显）：
   ```bash
   git ls-remote origin refs/heads/main    # 与 git rev-parse HEAD 逐字符比对
   ```
3. **禁止用管道吞退出码**：`git push 2>&1 | tail -5` 的退出码是 `tail` 的 `0`，
   会把**失败的推送读成成功**。要留输出就 `set -o pipefail`，或者干脆分开跑一条干净的 `git push`。
4. 如需检查每日归档，用 `git fetch origin data && git log FETCH_HEAD --oneline -5`，
   不要试图在 `main` 上找 `hits/`、`state/`。

## 3. 提交粒度（这条覆盖全局 AGENTS.md 的「分步 commit」）

全局规则说的「分步 commit」是指**把多阶段任务拆成几个提交**，不是每改一个文件就 commit + push 一次。

- 一次任务 ≈ 一个可交付单元 ≈ **1 个提交**；大改造最多 3 个（`feat` / `test` / `docs`）。
- 同一批文档微调（「清掉某句残留」「五家→六家」这种）**合并成一个 `docs:` 提交**，别一个文件一个提交。
- **不要把实验性/临时文件提交进 main**（例如临时 workflow 探测）。要试就放本地，或一次性分支，试完就删。
- 提交信息用 Conventional Commits：`feat|fix|docs|chore|test|refactor|ci|build(scope): 中文描述`。

## 4. 禁止项

- `git push --force` / `-f` / `--all` / `--mirror`：**一律不要用**。远端 main 是唯一真相，写错了就补一个 `revert` 提交。
- `git reset --hard`。
- 在仓库根以外执行 `git add -A` / `git add .`（会带上 `.venv`、`build/`、`dist/`、`logs/`）。
- 未确认 toplevel 就在 `~/Codex/*` 下跑 git。

## 5. 结束时怎么回报

一句话讲清四件事，不要贴大段日志：

1. 改了什么（文件级）
2. 提交号（`git log --oneline -1`）
3. `git ls-remote origin refs/heads/main` 的复核结果
4. 推完后远端是否还有未知提交（有没有被 CI 抢先）
