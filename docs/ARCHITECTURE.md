# 架构说明

本文说明 IndocQA 的分层结构、两条能力链路、数据流、核心模块职责与扩展方式。

---

## 1. 设计目标

| 目标 | 含义 |
| --- | --- |
| 引擎与领域解耦 | 引擎只提供机制；领域内容由声明式 Domain Pack 提供 |
| 行为可复现 | 检索与构建行为绑定 Build 快照，同一输入可重建等价结果 |
| 契约稳定 | API 只增不改；错误码与事件结构稳定 |
| 故障可隔离 | 两条链路独立路由与数据目录，构建链路异常可回退问答链路 |
| 存储可插拔 | 向量、关键词、对象存储通过适配器契约接入 |

设计原则：

1. **固定算法、受控配置**：算法与执行顺序不可配置，只开放预设与声明式输入。
2. **纯声明式 Pack**：Pack 只提供数据、规则、提示词风格与 UI 文案，不执行代码。
3. **控制面是事实源**：索引可重建；先写控制面，再通过 Outbox 同步索引。
4. **先包裹后拆分**：保持既有行为不变地接入新架构，再逐项删除硬编码。
5. **能力协商**：适配器缺少必需能力时拒绝构建，不静默降级。

---

## 2. 分层架构

```text
┌─────────────────────────────────────────────────────────────┐
│ 前端 web/src：问答 / 文档管理 / 引擎配置                      │
└───────────────┬─────────────────────────────┬───────────────┘
                │ /api                        │ /api/v1
┌───────────────▼──────────────┐ ┌────────────▼───────────────┐
│ 问答与知识运营链路            │ │ 控制面与构建编排链路        │
│ 意图 → 检索 → 上下文 → 生成   │ │ KB / Pack / Build / 检索    │
└───────────────┬──────────────┘ └────────────┬───────────────┘
                │                              │
┌───────────────▼──────────────┐ ┌────────────▼───────────────┐
│ 引擎层 app/engine            │ │ 控制面 app/control          │
│ Build、索引、入库、检索编排    │ │ SQLite 事实源、Outbox、对账 │
└───────────────┬──────────────┘ └────────────┬───────────────┘
                │                              │
┌───────────────▼──────────────────────────────▼───────────────┐
│ 契约层 app/contracts：配置 / 索引 / 嵌入 / 对象存储 / Pack / 检索 │
├──────────────────────────────────────────────────────────────┤
│ 适配层 app/adapters：Chroma / BM25 / 本地对象存储 / 嵌入模型    │
├──────────────────────────────────────────────────────────────┤
│ 插件层 server/packs：example-minimal / customer-service-faq   │
└──────────────────────────────────────────────────────────────┘
```

| 层 | 目录 | 允许依赖 | 禁止 |
| --- | --- | --- | --- |
| 契约层 | `server/app/contracts/` | 仅标准库与类型定义 | 依赖具体实现或 Pack |
| 引擎层 | `server/app/engine/` | 契约层 | import 具体 Pack；出现领域词 |
| 适配层 | `server/app/adapters/` | 契约层 | 业务规则 |
| 控制面 | `server/app/control/` | 契约层 | 检索算法 |
| 插件层 | `server/packs/` | 无（纯数据） | 可执行代码 |
| 接口层 | `server/app/api/` | 引擎层、控制面 | 领域分支 |
| 前端 | `web/src/` | HTTP API | 硬编码领域示例 |

---

## 3. 两条能力链路

两条链路运行在同一 FastAPI 进程中，路由前缀分别为 `/api` 与 `/api/v1`，但契约、事实源与生命周期不同。

### 3.1 问答与知识运营链路（`/api`）

**目标**：面向终端用户的问答体验与日常文档运营。

**能力**：文档解析与切块、混合检索、意图路由、多跳、术语扩展、上下文预算与去重、引用审计、安全过滤、会话与多模态附件。

**契约**：成功响应 `{data: ...}`；错误响应 `{error: {code, message, detail}}`；问答为 SSE 六事件。

**事实源**：当前索引与 `manifest`，操作即时生效。

### 3.2 控制面与构建编排链路（`/api/v1`）

**目标**：面向知识治理的可复现构建、版本切换与回滚。

**能力**：知识库、Domain Pack、Build、配置、检索、增量文档、替换/撤销、激活/回滚、对账、审计。

**契约**：统一错误包 `{error: {code, name, message, detail, retryable, requestId}}`；响应头 `X-API-Version: 1`；可选静态 Bearer 鉴权。

**事实源**：控制面 SQLite。索引是可重建的派生数据，通过 Outbox 与对账保持一致。

### 3.3 桥接与回退

`POST /api/chat` 携带 `kbId` 且该 KB 有 active Build 时，问答链路可调用构建链路的 Build 级检索：

```text
/api/chat → 解析请求 → 判断 answer_backend
  auto / engine 且 kbId 对应 KB 有 active Build
    → retrieve_from_engine（Build 级混合检索）
    → 失败则记录 trace 并回退问答链路检索
  否则
    → 问答链路检索
```

前端默认不传 `kbId`，因此默认走问答链路；构建链路通过引擎配置页、CLI 与外部 API 使用。

---

## 4. 核心模块

| 模块 | 路径 | 职责 |
| --- | --- | --- |
| API 路由 | `server/app/api/` | `/api` 路由、SSE、上传、会话、文档与目录 |
| V1 路由 | `server/app/api/v1/` | 控制面资源端点、错误包、Build 与文档编排 |
| 契约 | `server/app/contracts/` | Protocol 与值对象：配置、索引、嵌入、对象存储、Pack、检索阶段 |
| 控制面 | `server/app/control/` | SQLite schema 与仓储、Build 指针、审计、Outbox、对账 |
| 适配器 | `server/app/adapters/` | Chroma 向量、BM25 关键词、本地对象存储、Ollama/远程嵌入 |
| 引擎 | `server/app/engine/` | EnginePipeline、运行时装配、Build 入库、模型注册表、检索桥 |
| Pack | `server/app/packs/` | 发现、加载、校验、注册、字段级 diff、运行时覆盖与 UI Profile |
| 检索 | `server/app/retrieval/` | 意图分类、混合检索、术语簇、重排、弱命中闸门 |
| 上下文 | `server/app/context/` | 预算、去重、层级组装、模板、会话画像 |
| 生成 | `server/app/generation/` | LLM 网关、引用审计、安全过滤、JSON 格式化 |
| 索引 | `server/app/indexing/` | 解析入库、目录迁移、manifest、任务、BM25 侧车 |
| 解析 | `server/app/parsing/` | PDF / Markdown / TXT 解析与父子切块 |
| 分类 | `server/app/classification/` | AI 目录分类与反馈 |
| 会话 | `server/app/session/` | SQLite 会话、消息、TTL 清理与压缩 |
| 附件 | `server/app/attachments/` | 分片上传、断点续传、附件解析与临时目录 |
| CLI | `server/app/cli.py` | `init / validate / build / query` |

---

## 5. 数据模型与命名空间

### 5.1 控制面实体

| 实体 | 说明 |
| --- | --- |
| KB | 知识库元信息、Pack 绑定、active/previous Build |
| Build | 状态、配置快照、Pack 快照、嵌入指纹、语料修订号 |
| Document | 文档注册、内容哈希、所属 Build 与来源 |
| Chunk | 分块内容、位置、父子关系 |
| Operation / Audit | 操作记录、审计事件 |
| Outbox | 控制面到索引的待同步事件 |

### 5.2 索引命名空间

- 每个 Build 拥有独立的向量集合与 BM25 侧车，命名包含 `kbId` 与 `buildId`。
- active/previous 指针切换是原子操作；切换后旧 Build 保留为 `archived`，可按需回滚。
- 文档替换保留撤销快照；撤销仅作用于最近一次替换。

### 5.3 Build 状态机

```text
draft → ready → active → archived
                 ↑        │
                 └────────┘  （rollback / activate）
```

- `ready + warning`：部分文档失败，可激活，但需查看 `stats.failed`。
- `blocked`：零可用分块或全量失败，禁止激活与检索。

---

## 6. 配置分层

| 层 | 作用域 | 示例 |
| --- | --- | --- |
| 引擎默认 | 进程 | 嵌入后端、维度、检索超时 |
| KB 配置 | 单个知识库 | 预设、分块参数、Pack 绑定 |
| 回答配置 | 生成与上下文 | 上下文预算、引用策略 |
| UI 配置 | 前端展示 | 主题、文案 |
| Pack | 领域声明 | 术语、目录、意图、提示词 |

合并顺序为引擎默认 → Pack → KB → 请求级覆盖；生效值与来源可通过 `/api/v1/kbs/{kb}/config` 查看。

---

## 7. 扩展方式

### 7.1 新增领域

1. 在 `server/packs/<id>/<version>/` 创建 Pack，填写 `manifest.json` 与所需声明文件。
2. 使用 `POST /api/v1/packs/validate` 校验，再 `POST /api/v1/packs/install` 安装。
3. 绑定到 KB，创建并激活新 Build。
4. 运行领域词守卫与评测，确认引擎目录无领域词汇。

### 7.2 新增适配器

1. 在 `server/app/contracts/` 定义或复用 Protocol。
2. 在 `server/app/adapters/` 实现适配器，声明能力集。
3. 在 `server/app/engine/runtime.py` 装配，能力不足时拒绝构建。
4. 补充一致性测试与契约测试。

---

## 8. 一致性与故障处理

- **先控制面后索引**：写操作先落 SQLite，再通过 Outbox 同步索引；失败可重放。
- **对账**：`POST /api/v1/builds/{build}/reconcile` 清理孤儿、补建缺失分块。
- **回退**：构建链路检索异常时自动回退问答链路，并在 `trace` 中记录原因。
- **幂等**：文档注册按内容哈希幂等；Pack 同 `id+version` 内容不可变。
- **鉴权失败**：`/api/v1` 返回 `40101`，不暴露内部错误细节。

---

## 9. 可观测性

| 能力 | 位置 |
| --- | --- |
| 请求 ID | 响应头 `X-Request-Id`，错误包 `requestId` |
| 错误码 | 数字 + 稳定名称 + `retryable` |
| 健康检查 | `/api/health`、`/api/v1/health` |
| 审计 | 控制面审计事件与操作记录 |
| SSE 轨迹 | `trace` 事件记录检索、意图、回退等阶段 |

---

## 10. 约束

- 单进程单 worker：控制面 SQLite、Chroma、BM25 侧车与对象存储均为进程内资源。
- 跨维度嵌入模型不可混用；切换需重建 Build。
- 构建链路使用基础混合检索，不包含问答链路的自适应与多跳能力。
- Pack 为纯声明式，不支持执行代码或修改算法。
