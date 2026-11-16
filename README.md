<p align="center">
  <a href="https://peifeng.li"><img width="184px" alt="logo" src="https://raw.githubusercontent.com/li-peifeng/AVdb-Only/refs/heads/main/public/logo.svg" />
  </a>
</p>
<p align="center">
  <a href="https://hub.docker.com/r/leolitaly/avdb">
    <img src="https://img.shields.io/docker/pulls/leolitaly/avdb?color=#48BB78&logo=docker&label=pulls" alt="Downloads" />
  </a>
</p>

# Jav Actors Mapping x AVdb

## 项目说明

本仓库用于维护 AVdb 使用的演员映射表，主要目的是统一演员名称与别名映射，方便在 AVdb 中进行检索、匹配和展示。

### 强烈感谢 jzdxjk 大佬的整理，收集 
## 适用范围

- 本仓库内容仅适用于 AVdb。
- 其他项目如需使用，请自行评估兼容性并自行维护，不保证可直接复用。

## 仓库结构

- actor-mapping.xml：演员映射数据文件（XML 格式）。
- mapping_actor.xml：自动生成的精简映射表，根节点为 actor，仅保留 zh_cn、zh_tw、jp、keyword，使用两空格缩进，不包含外部 ID、简介、其他属性和黑名单。请维护源文件，不要手工修改此文件。
- scripts/format_actor_mapping.py：映射格式化与校验脚本（natural 排序、转译字符串检查、同步精简映射表）。
- scripts/merge_avdb_mapping.py：只读合并 AVDB 数据库、导出 XML/JSON、旧映射 XML 和演员采集 JSON，支持预览、覆盖检查与合并报告。
- scripts/resolve_duplicate_person_ids.py：补充 TMDB 重名演员，支持完整 XML 的嵌套结构，写入时同步精简表。
- tests/test_mapping.py：页面 ID、合并、来源覆盖与精简表同步的回归测试。
- .github/workflows/actor-mapping-format.yml：自动化格式化与校验工作流。

## 贡献流程

如需添加或修改映射内容，请按以下流程提交：

1. Fork 本仓库到你的账号。
2. 在 Fork 仓库中修改 actor-mapping.xml 并提交 commit。
3. 发起 Merge Request（合并请求），并说明本次变更内容与原因。
4. 等待维护者审核与合并。

## 合并后更新

- 合并请求通过后，相关更新可直接推送到 AVdb。
- 如果审核中提出修改意见，请按意见补充后再次提交。

## 自动化 Actions

仓库已配置 actor-mapping.xml 的自动格式化与校验流程：

1. 当 push 或 pull request 涉及任一 XML、格式化脚本或对应工作流时，工作流会自动触发。
2. pull request 场景执行严格校验：
  - 对 actor 内的条目进行 natural 排序。
  - 统一属性顺序为 zh_cn、zh_tw、jp、keyword、page_id、tmdb_id（简介等属性随后排列）。
  - 检查 page_id 格式，以及 page_id、tmdb_id 的唯一性。
  - 检查并清理多余转译字符串（例如重复实体转义、数字实体转义、可疑反斜杠转义串）。
  - 在工作区生成精简表，并检查源文件是否已规范格式化；贡献者只需修改 actor-mapping.xml，无需手工同步精简表。
3. push 场景自动执行格式化，并同步生成 mapping_actor.xml；任一 XML 有变更时，将两个文件的变更一并自动提交回当前分支。

`jzdxjk` 的自动审批与合并允许 PR 修改 actor-mapping.xml、mapping_actor.xml 中的任一文件或两者，但不允许夹带其他文件。贡献者仍可只提交源文件；自动合并成功后，工作流会在合并目标分支直接同步精简表并提交，无需向贡献者的 Fork 写入文件，也无需贡献者拥有本仓库的写权限。

### 本地预检查

提交前建议先执行：

```bash
python -m pip install pypinyin
python scripts/format_actor_mapping.py --check actor-mapping.xml
```

如需本地直接格式化：

```bash
python scripts/format_actor_mapping.py --write actor-mapping.xml
```

`--write` 会同时更新源文件所在目录下的 mapping_actor.xml，即使源文件已经符合格式，也会补建或更新精简表。`--check` 会检查源文件格式、转义字符串及精简表是否同步；需要同步时先执行上述 `--write` 命令再检查。

### 演员页面 ID

完整表中的可选属性 `page_id` 对应 JavDB 演员页面短 ID。例如页面 `https://javdb.com/actors/k2Ne` 对应 `page_id="k2Ne"`。它与 AVDB 数据库内的数字主键 `id`、TMDB 的 `tmdb_id` 分别独立，不保存完整链接。

- 仅允许 1 至 120 位英文字母或数字，区分大小写；同一页面 ID 只能出现在一条演员记录中。
- 未知时省略属性，不填写空字符串、完整 URL 或占位值。
- `mapping_actor.xml` 继续仅保留四个名称/别名字段，不包含页面 ID；需要页面 ID 时读取完整表。

### 合并 AVDB 与其他映射来源

先预览，确认报告后使用同一命令加 `--write` 写入：

```bash
python scripts/merge_avdb_mapping.py /path/to/avdb.db /path/to/legacy-mapping.xml /path/to/actor-all.json
python scripts/merge_avdb_mapping.py /path/to/avdb.db /path/to/legacy-mapping.xml /path/to/actor-all.json --write
python scripts/format_actor_mapping.py --check actor-mapping.xml
python -m unittest discover -s tests -v
```

来源也可以是 AVDB 导出的完整 XML 或页面 ID JSON。数据库使用只读连接，并沿用 AVDB 导出范围，排除 Emby 和 external 来源的演员；不会修改数据库或传入的文件。预览只写报告，默认位置为 `.cache/avdb-merge-report.json`，可用 `--report` 指定。`--xml` 可指定待合并的源表路径。

合并规则：

- 保留仓库已有名称和非空资料，补齐缺失字段，并将来源中的其他名称与别名加入 `keyword`；不删除已有演员或别名。
- 优先按页面 ID、TMDB ID、完整主名组合、主名和别名依次匹配，名称采用逐项精确比较。
- 不同的已知页面 ID 或 TMDB ID 不因共用别名而合并。归属不明确的记录保留为独立条目，并写入报告；不根据遍历顺序猜测演员。
- 旧 XML 的标准 JavDB `href` 会转换为 `page_id`；`actor-all.json` 的 `id` 只有与标准 `href` 一致时才作为页面 ID。其他网站或格式错误的链接只记录到报告，保留演员资料，不猜测 ID。主名字段中误填的逗号分隔别名会拆分保留。
- 所有来源先完成格式、ID 唯一性和覆盖检查，要求原表及每个来源的有效 ID、名称、别名均在结果中保留，才写入两个 XML。重复合并相同来源不应增加记录。

Actions 会在映射文件、`scripts/` 或 `tests/` 变更时运行回归测试，并继续自动生成精简表。受信任贡献者的自动审批文件白名单仍限于两份 XML。

## 提交建议

- 每次提交尽量聚焦单一问题，便于审查和回溯。
- 修改时请保持 XML 结构和命名风格一致，避免无关格式化改动。

## 详细修改规则

### 1. 新增映射规则

1. 新增前先全文检索，确认该演员未以主名或别名形式存在，避免重复条目。
2. 每个演员使用一条 a 节点记录，不要拆成多条。
3. 优先补齐 zh_cn、zh_tw、jp 三个名称字段，再维护 keyword 别名集合。
4. keyword 中应包含常见检索名、历史曾用名、简繁体差异名和常见拉丁拼写（如有）。
5. 若存在权威外部 ID（例如 tmdb_id），可补充为可选属性。

### 2. 修改现有映射规则

1. 修改主名称字段（zh_cn、zh_tw、jp）时，需要在合并请求描述里写明依据。
2. 删除 keyword 中别名前，请确认该别名不会影响历史数据检索。
3. 如果只是补充别名，优先在原条目上追加 keyword，不要新建重复条目。
4. 遇到重复演员条目时，请在合并请求中说明合并策略（保留哪条、迁移哪些别名）。

### 3. 不建议的改动

1. 不要进行与本次需求无关的大规模重排或批量格式化。
2. 不要只改空格、换行、缩进而不改数据内容。
3. 不要混入与演员映射无关的文件变更。

## 格式要求

### 1. 文件级要求

1. 文件名固定为 actor-mapping.xml。
2. 文件编码使用 UTF-8。
3. 保留 XML 头声明：<?xml version="1.0" encoding="UTF-8"?>。
4. 根节点保持为 actor-mapping

### 2. 条目结构要求

1. 每条记录使用自闭合标签：<a ... />。
2. 建议属性顺序固定为：zh_cn、zh_tw、jp、keyword、page_id（可选）、tmdb_id（可选）。
3. zh_cn、zh_tw、jp 三个字段应填写演员对应名称；缺失信息时应在合并请求中说明。
4. keyword 使用英文逗号分隔多个别名，不加空格，例如：keyword="别名A,別名A,AliasA"。
5. keyword 内避免重复词；避免首尾逗号；避免连续逗号。

### 3. 排序与可维护性要求

1. 新增条目时尽量按现有顺序插入，减少后续冲突。
2. 保持单行单条目，便于审查与差异比较。
3. 避免修改未涉及条目的内容，控制变更范围。

### 4. 推荐条目模板

```xml
<a zh_cn="中文简体名" zh_tw="中文繁体名" jp="日文名" keyword="别名1,別名1,Alias1" />
```

如需附加外部 ID，可使用：

```xml
<a zh_cn="中文简体名" zh_tw="中文繁体名" jp="日文名" keyword="别名1,別名1,Alias1" tmdb_id="123456" />
```

同时附加 JavDB 页面 ID：

```xml
<a zh_cn="中文简体名" zh_tw="中文繁体名" jp="日文名" keyword="别名1,別名1,Alias1" page_id="k2Ne" tmdb_id="123456" />
```
