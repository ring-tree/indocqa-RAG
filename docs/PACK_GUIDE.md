# Domain Pack 编写指南

本文档说明如何新增一个领域 Pack；完整示例见 `server/packs/example-minimal/1.0.0/`，客服 FAQ 示例见 `customer-service-faq`。

## 1. 目录结构

```text
server/packs/<pack-id>/<version>/
  manifest.json         # 必需
  terms.json            # 术语同义簇（种子）
  parser.json           # 跳过文件
  folders.json          # 目录模板/默认目录/迁移映射
  classification.json   # 文件名规则/文档类型/标题清洗
  intent.json           # 意图关键词/连接词/编号正则/例外
  data-facts.json       # 数据事实单位/关键词
  authority.json        # 文档类型权威度
  persona.json          # 角色词
  prompts.json          # 回答风格（只追加，不覆盖核心规则）
  ui.json               # 副标题/示例/目录示例
  eval.json             # 评测集路径
```

只允许数据文件；禁止 `.py/.js/.sh/.exe` 等可执行文件、符号链接、超大文件。

## 2. 最小示例

`manifest.json`：

```json
{
  "id": "example-minimal",
  "version": "1.0.0",
  "domain": "demo",
  "languages": ["zh"],
  "engine_range": ">=0.1,<1.0",
  "description": "最小示例 Pack"
}
```

`terms.json`：

```json
{"clusters": {"示例": ["样例", "演示"]}}
```

`folders.json`：

```json
{"template": ["文档", "评测夹具"], "default": "未分类", "migration": {}}
```

`ui.json`：

```json
{"subtitle": "示例知识库", "examples": ["这个示例 Pack 怎么用？"], "folder_example": "文档"}
```

其余块可省略，引擎会使用默认值。

## 3. 字段所有权

| 层 | 可覆盖 | 不可覆盖 |
| --- | --- | --- |
| Pack | 术语、分类、意图、目录、权威、角色、风格、UI | 引用/拒答/安全核心、检索算法阶段 |
| KB | 预设、阈值、模型选择 | `engine.*` 锁定字段 |
| Answer/UI | 风格与文案 | 引用格式与置信度语义 |

## 4. 校验与安装

```bash
cd server
uv run python -c "from app.packs.loader import PackLoader; p=PackLoader('packs', engine_version='0.1.0').load('example-minimal','1.0.0'); print(p.pack.manifest.id, p.content_hash)"
```

API：

```bash
curl -X POST http://127.0.0.1:8010/api/v1/packs/validate \
  -H 'Content-Type: application/json' \
  -d '{"packId":"example-minimal","version":"1.0.0"}'
```

安装与绑定：

```bash
curl -X POST http://127.0.0.1:8010/api/v1/packs/install \
  -H 'Content-Type: application/json' \
  -d '{"packId":"example-minimal","version":"1.0.0"}'
curl -X POST http://127.0.0.1:8010/api/v1/kbs/<kb>/pack \
  -H 'Content-Type: application/json' \
  -d '{"packId":"example-minimal","version":"1.0.0"}'
```

## 5. 生命周期

- 同一 `id+version` 内容不可变；修改必须发新版本；
- 被 KB 绑定的 Pack 不能卸载；
- 创建 Build 时写入 Pack 快照，卸载后历史 Build 仍可复现；
- `GET /api/v1/packs/diff?packId=&fromVersion=&toVersion=` 返回字段级差异与重建模式。

## 6. 测试要求

1. `PackLoader` 校验通过；
2. 无其他领域词（参考 `tests/test_domain_leakage.py`）；
3. 提供合成语料与评测集，至少覆盖 term/param/procedure/fault/multihop；
4. 用独立数据目录启动，完成建库、构建、查询、引用与回滚；
5. 全量 `pytest` 通过。

## 7. 术语缓存与升级

术语缓存 `terms.json` 会记录 Pack 的 `id/version/contentHash`：

- 哈希一致：直接复用缓存；
- 哈希变化（Pack 升级）：先清除 BM25 侧车旧“同义”标签，再重建簇并重新打标；
- 旧缓存没有哈希：首次使用时只补写当前哈希，不重建，保持既有检索结果；
- KB 解绑 Pack：重建为无 Pack 种子状态，并清除旧标签。

因此升级 Pack 的 `terms.json` 后无需手工删除缓存；分类、意图、提示词为实时读取，不受缓存影响。
