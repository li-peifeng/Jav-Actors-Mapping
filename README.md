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

## 适用范围

- 本仓库内容仅适用于 AVdb。
- 其他项目如需使用，请自行评估兼容性并自行维护，不保证可直接复用。

## 仓库结构

- actor-mapping.xml：演员映射数据文件（XML 格式）。
- scripts/format_actor_mapping.py：映射格式化与校验脚本（natural 排序、转译字符串检查）。
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

1. 当 push 或 pull request 涉及 actor-mapping.xml 时，工作流会自动触发。
2. pull request 场景执行严格校验：
  - 对 <actor></actor> 内的 <a ... /> 条目进行 natural 排序。
  - 统一属性顺序为 zh_cn、zh_tw、jp、keyword、tmdb_id（其他属性按名称追加）。
  - 检查并清理多余转译字符串（例如重复实体转义、数字实体转义、可疑反斜杠转义串）。
3. push 场景自动执行格式化，并在 actor-mapping.xml 有变更时自动提交回当前分支。

### 本地预检查

提交前建议先执行：

```bash
python scripts/format_actor_mapping.py --check actor-mapping.xml
```

如需本地直接格式化：

```bash
python scripts/format_actor_mapping.py --write actor-mapping.xml
```

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
4. 根节点保持为 actor。

### 2. 条目结构要求

1. 每条记录使用自闭合标签：<a ... />。
2. 建议属性顺序固定为：zh_cn、zh_tw、jp、keyword、tmdb_id（可选）。
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
