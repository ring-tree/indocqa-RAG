# 迁移指南：问答单链路部署 → 控制面 + Build 架构

> 适用范围：从仅使用问答与知识运营链路（`/api`）的部署，升级到「控制面 + Build 命名空间 + Domain Pack」的构建编排架构。
> 原则：问答与知识运营链路可继续服务；控制面与构建编排链路上线并行；任何一步失败都能回滚。

## 1. 迁移前检查

| 项 | 命令 / 位置 | 通过标准 |
| --- | --- | --- |
| 代码版本 | `git rev-parse --short HEAD` | 记录当前版本，便于回滚 |
| 问答链路健康 | `GET /api/health` | 200 且 `kb.state=ready` |
| 会话与附件 | `server/data/db`、`server/data/attachments` | 已确认保留策略 |
| 环境变量 | `server/.env` | 生成模型、嵌入后端可用 |
| 磁盘空间 | 备份 + 新索引 | 至少 2 倍当前 `server/data` 大小 |

## 2. 备份

```powershell
# Windows PowerShell
$stamp = Get-Date -Format yyyyMMddHHmmss
$backup = "backup-$stamp"
New-Item -ItemType Directory -Force "$backup" | Out-Null
Copy-Item server\.env "$backup\.env"
Copy-Item server\data "$backup\server-data" -Recurse
Copy-Item data\terms "$backup\terms" -Recurse
Copy-Item server\packs "$backup\packs" -Recurse
```

```bash
# Linux / macOS
stamp=$(date +%Y%m%d%H%M%S)
mkdir -p "backup-$stamp"
cp server/.env "backup-$stamp/.env"
cp -r server/data "backup-$stamp/server-data"
cp -r data/terms "backup-$stamp/terms"
cp -r server/packs "backup-$stamp/packs"
```

备份必须包含：控制面 SQLite、Chroma 目录、BM25 侧车、对象存储、术语缓存、Pack 资产、`.env`。

## 3. 升级代码与依赖

```bash
git fetch
git checkout <目标版本>
cd server
uv sync --frozen --extra dev
uv run pytest -q
```

控制面 SQLite 的 schema 迁移在 `ControlPlane` 初始化时自动执行（`PRAGMA user_version`）；问答链路的索引与 `data/terms` 不需要转换。

## 4. 构建编排链路并行上线

1. 选择 Domain Pack：`server/packs/<id>/<version>/`；
2. 初始化构建编排项目（独立数据目录）：

   ```bash
   cd server
   uv run python -m app.cli init --dir ../deploy/demo \
     --name "生产知识库" --pack <pack-id>@<version>
   ```

3. 把语料放入 `deploy/app/data/corpus`；
4. 校验并构建：

   ```bash
   uv run python -m app.cli validate --dir ../deploy/demo
   uv run python -m app.cli build --dir ../deploy/demo
   ```

5. 验证查询：

   ```bash
   uv run python -m app.cli query --dir ../deploy/demo "核心业务问题"
   ```

6. 启动后端并验证 `/api/v1/health`、`/api/v1/kbs`、`/api/v1/packs`。

7. 如需让 `POST /api/chat` 直接使用 Build 级检索，在请求中带 `kbId`，并保持 `INDOCQA_ANSWER_BACKEND=auto`（默认）；该 KB 有 active Build 时自动走 Build 检索，否则回退问答链路。

> 问答链路的 Chroma 索引不会自动转换为 Build 命名空间。构建链路按 Build 重新入库，这是保证「历史 Build 可复现」的设计选择。

## 5. 验证清单

| 检查 | 命令 | 通过标准 |
| --- | --- | --- |
| 契约 | `uv run pytest tests/test_api_v1.py tests/test_chat_api.py -q` | 全通过 |
| 配置 | `uv run python -m app.cli validate --dir <项目>` | 返回 0 |
| 构建 | `uv run python -m app.cli build --dir <项目>` | Build `active`，文档/块数非 0 |
| 查询 | `uv run python -m app.cli query --dir <项目> "<问题>"` | 命中来源与预期一致 |
| Pack | `POST /api/v1/packs/validate` | `valid=true` |
| 领域词 | `uv run pytest tests/test_domain_leakage.py -q` | 命中 0 |
| SSE | `POST /api/chat` | `trace/context/sources/token/done` 顺序不变 |

## 6. 回滚

### 6.1 应用层回滚（Build）

```bash
curl -X POST http://127.0.0.1:8010/api/v1/kbs/<kb>/rollback \
  -H 'Content-Type: application/json' -d '{"buildId":"<上一个 Build>"}'
```

### 6.2 配置回滚

把备份中的 `indocqa.json`、`.env`、Pack 资产恢复原位，重新执行 `validate`。

### 6.3 完整回滚

1. 停止服务；
2. 恢复 `server/data`、`data/terms`、`server/packs` 与 `.env`；
3. `git checkout <迁移前版本>`；
4. `uv sync --frozen --extra dev`；
5. 启动并验证 `GET /api/health` 与 40 题检索指标。

## 7. 已知限制

- 两条链路共享单 worker 约束，不支持多进程写入（`ISSUE-B04`）；
- `engine` 后端使用 Build 级基础混合检索，不含问答链路的意图路由、多跳与术语扩展；默认 `auto` 仅在有 `kbId` 且 KB 有 active Build 时启用；
- Pack 术语升级会自动失效缓存，并在重建前清除 BM25 旧标签；升级后首次查询可能有一次性重建开销；
- 首 Token 延迟仍受上游模型与现有生成链路影响（`ISSUE-B05`），多跳题覆盖有限（`ISSUE-B06`）；
- Build 级嵌入模型仅支持与部署维度一致的模型（默认 1024 维），不支持跨维度混用；切换模型需重建 Build 索引；
- 增量入库、替换与撤销仅作用于 active Build；零可用分块的 Build 会被置为 `blocked`，部分文件失败时为 `ready + warning`，需查看 `stats.failed`；
- `/api/v1` 的静态 Bearer 鉴权为单 token，适合单租户部署；多租户与细粒度权限仍需外部网关。
