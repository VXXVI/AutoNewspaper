# 自定义报社

> 本文由原 `README.md` 迁出。看板相关操作见 [dashboard.md](dashboard.md)。

除内置六家外，可**在看板「报社与抓取规则」里新增自定义报社**，也可直接手编配置文件。
如果手编后的 JSON 损坏，界面的保存/删除会返回冲突提示并保留原文件，不会用空配置覆盖。

**文件位置**：`<项目根>/papers.custom.json`（**进 git**）。⚠️ 它跟随**项目根**
（`paths.find_project_root()`），**不跟 `--data-dir` 走**——`--data-dir` 只是数据落点
（`state/` / `hits/`），自定义报社属于**配置层**。所以用 `--data-dir /tmp/x` 跑看板时，
新增的报社仍写回项目根。

```json
{
  "version": 1,
  "disabled": ["羊城晚报"],
  "papers": [
    {
      "name": "某某日报",
      "style": "ymd",
      "tpl": "https://example.com/epaper/html/layout/{a}/node_01.html",
      "node_pattern": "node_\\w+\\.html?$",
      "article_pattern": "content_\\w+\\.html?$",
      "timeout": 30,
      "retries": 2,
      "node_exclude": "/pad/"
    }
  ]
}
```

顶层 `disabled` 是可选的报社名称数组；缺省时按空数组处理，兼容旧文件。名单里的报社
只会停止参与后续扫描，配置和 `hits/archive.json` 历史都保留；看板的「停用 / 恢复」会
安全维护这个字段。纯自定义报社需要彻底移除时，再使用「删除」。

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | ✅ | 报社名，非空、≤ 30 字符，中文即可 |
| `style` | ✅ | `ymd` → `202609/22`；`dash` → `2026-09/22`；`custom` → 使用 `date_format` |
| `date_format` | custom 时必填 | 例如 `%Y/%m/%d`、`%Y%m%d`；只允许年月日指令与路径分隔符 |
| `tpl` | ✅ | 第 1 版链接模板，**必须含 `{a}`**，且以 `http://` / `https://` 开头 |
| `node_pattern` | ❌ | 版面链接正则；默认 `node_\w+\.html?$` |
| `article_pattern` | ❌ | 文章链接正则；默认 `content_\w+\.html?$` |
| `timeout` | ❌ | 单次抓取超时秒数，5 ~ 120（缺省用全局默认） |
| `retries` | ❌ | 重试次数，0 ~ 6 |
| `node_exclude` | ❌ | 正则，用于排除手机版镜像等重复版面链接（须能编译通过） |

**同名覆盖语义**：`name` 与内置重复时**覆盖内置那条**（可用来改内置报社的 URL / 超时）；
不重复则**追加**。合并后**内置原有顺序在前**，纯新增按文件顺序追加在后。
看板里对应标注：`内置` / `覆盖` / `自定义`。

**⚠️ 关键提醒**：新增后写的是本机的 `papers.custom.json`。**要让每日 CI（GitHub Actions）
扫描也包含这家，必须把它 `git add` 后提交并推送**——本机加完并不等于 CI 会抓。
看板新增成功后的状态行也会显式提示这一点。

**容错**：文件缺失 / JSON 非法 / 单条非法 → **忽略该条并向 stderr 打一行警告**，
绝不让整个扫描崩掉。

默认解析按方正系 / 人民网系的三段式 `node_* → content_*` 工作；路径命名不同的站点可用
`node_pattern` / `article_pattern` 覆盖链接规则，日期目录不同可用 `style=custom` +
`date_format`。正文仍需能被通用标题/正文提取器识别；如果 HTTP 200 但解析不到版面或文章，
扫描会明确报「结构异常」并以非零退出，不会静默当成无命中。**新增后先用“试抓”探测**。

**SSRF 能力边界（如实说明，不封）**：`tpl` 是用户自己填的 URL，扫描器会用系统 `curl`
去抓。这里**只允许 `http` / `https` 前缀**，**不封私网 IP 段**——因为用户很可能就是想扫
内网报社。这是本机单人工具，风险可接受；但请勿在多用户/公网暴露的场景使用。
