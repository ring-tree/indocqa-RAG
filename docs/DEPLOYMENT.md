# 部署与运维指南

本文档覆盖本地部署、依赖说明、一键启动、配置项、首次建库、生产建议与常见问题排查。目标：在一台干净的开发机上 **10 分钟内**跑通问答与文档管理。

## 1. 部署拓扑与端口

```
浏览器 ──► Vite 开发服务器 :5174 ──/api 代理──► FastAPI :8010
                                                ├─ Chroma（本地目录）
                                                ├─ SQLite（会话）
                                                └─ Ollama :11434（可选，bge-m3 嵌入）
```

| 组件 | 默认地址 | 说明 |
| --- | --- | --- |
| 前端 | `http://127.0.0.1:5174` | Vite，`VITE_PORT` 可改 |
| 后端 | `http://127.0.0.1:8010` | FastAPI + uvicorn |
| 接口文档 | `http://127.0.0.1:8010/docs` | Swagger UI |
| Ollama | `http://localhost:11434` | 可选；未安装时自动回退本地嵌入 |

> **单进程约束**：会话 SQLite、BM25 索引、Chroma 写入锁均为进程内单例，请只启动 **1 个 uvicorn worker**，不要使用 `--workers N` 或 Gunicorn 多进程。

## 2. 环境依赖

| 依赖 | 版本要求 | 安装方式 |
| --- | --- | --- |
| Python | ≥ 3.11（推荐 3.13） | [python.org](https://www.python.org/) |
| uv | 最新版 | `pip install uv` 或 [astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/) |
| Node.js | 20+（推荐 22） | [nodejs.org](https://nodejs.org/) |
| npm | 随 Node 安装 | — |
| Ollama（可选） | 最新版 | [ollama.com](https://ollama.com/)，并执行 `ollama pull bge-m3` |

硬件建议：8 GB 内存起步；使用 Ollama `bge-m3` 时建议 16 GB。生成模型为本项目外部依赖（OpenAI 兼容 API），不占用本机显存。

## 3. 五分钟手动部署

```bash
# 1. 获取代码
git clone <your-repo-url> indocqa
cd indocqa

# 2. 配置环境变量
cp server/.env.example server/.env
#    编辑 server/.env，至少填写：
#    INDOCQA_LLM_BASE_URL / INDOCQA_LLM_API_KEY / INDOCQA_LLM_MODEL
#    不填也能启动：知识库问答会降级为原文摘录/拒答，直接对话不可用

# 3. 安装后端依赖
cd server
uv sync --extra dev

# 4. 首次建库（内置语料 + 术语簇，幂等）
uv run python -m app.indexing.ingest ../data/corpus
uv run python -m app.retrieval.terms

# 5. 启动后端
uv run uvicorn app.main:app --host 127.0.0.1 --port 8010

# 6. 另开终端启动前端
cd web
npm ci            # 首次；或 npm install
VITE_API_TARGET=http://127.0.0.1:8010 VITE_PORT=5174 npm run dev
```

打开 `http://127.0.0.1:5174`，即可在“问答”页提问、在“文档管理”页上传文档。

## 4. 一键启动脚本

仓库提供 Windows 与 macOS/Linux 两个脚本，自动完成依赖安装、首次建库、后端与前端启动。

### Windows PowerShell

```powershell
# 完整启动
powershell -ExecutionPolicy Bypass -File scripts/start.ps1

# 只安装依赖并建库，不启动服务
powershell -ExecutionPolicy Bypass -File scripts/start.ps1 -InstallOnly
```

### macOS / Linux

```bash
bash scripts/start.sh
bash scripts/start.sh --install-only
```

脚本行为：

1. 检查 `uv` / `node` / `npm`；
2. 若 `server/.env` 不存在，从 `.env.example` 复制并提示填写 Key；
3. `uv sync --extra dev` 安装后端依赖；
4. 若 `server/data/chroma/manifest.json` 不存在，自动建库与术语簇；
5. 启动后端 `8010` 与前端 `5174`，日志写入 `logs/backend.log`、`logs/frontend.log`。

## 5. 关键配置项

配置文件：`server/.env`（从 `server/.env.example` 复制），完整说明见文件内注释。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `INDOCQA_LLM_BASE_URL` | — | OpenAI 兼容接口地址 |
| `INDOCQA_LLM_API_KEY` | — | 主供应商密钥 |
| `INDOCQA_LLM_MODEL` | — | 主模型名 |
| `INDOCQA_LLM_PROFILE_<N>_*` | — | 扩展供应商；主供应商失败后按顺序切换，前端下拉展示 |
| `INDOCQA_LLM_STREAM_IDLE_TIMEOUT` | 30 | 流式空闲超时（秒），超时切换下一个候选模型 |
| `INDOCQA_RETRIEVAL_TIMEOUT` | 45 | 检索整体超时（秒） |
| `INDOCQA_EMBED_BACKEND` | auto | `auto` / `local` / `ollama` / `minilm` |
| `INDOCQA_EMBED_OLLAMA_MODEL` | bge-m3 | Ollama 嵌入模型 |
| `INDOCQA_EMBED_LOCAL_MODEL` | BAAI/bge-small-zh-v1.5 | 本地 fastembed 模型 |
| `INDOCQA_RETRIEVAL_MODE` | adaptive | `adaptive` 生产；`fixed` 基线对照 |
| `INDOCQA_CONTEXT_MODE` | on | `on` / `shadow` / `off` |
| `INDOCQA_GENERATION_MAX_TOKENS` | 1024 | 固定生成长度，外部不可覆盖 |
| `INDOCQA_CHROMA_DIR` | data/chroma | 向量库目录 |
| `INDOCQA_SESSION_DB` | data/db/sessions.db | 会话 SQLite |

### 嵌入后端选择

| 模式 | 适用场景 | 说明 |
| --- | --- | --- |
| `ollama` | 质量优先，本机有 Ollama | 需 `ollama pull bge-m3`；检索质量最好 |
| `local` | 离线/零外部依赖 | 权重在 `models/`，进程内 ONNX |
| `minilm` | 兜底 | Chroma 内置，弱网下可能秒级校验延迟 |
| `auto` | 默认 | Ollama 可用则用，否则回退 local |

> 切换嵌入后端后，旧向量库不可用（集合名包含后端标识）。需删除 `server/data/chroma/` 并重新建库，或执行 `python -m app.indexing.ingest --rebuild`。

## 6. 建库与重建索引

```bash
cd server

# 增量建库（幂等，按内容哈希跳过重复）
uv run python -m app.indexing.ingest ../data/corpus

# 重建全部索引（切换嵌入后端、模型或分块策略后）
uv run python -m app.indexing.ingest ../data/corpus --rebuild

# 仅重建术语簇
uv run python -m app.retrieval.terms
```

Web 端也可在“文档管理”页上传文档，单个文件 ≤ 20 MB。

## 7. 生产部署建议

1. **单进程运行**：`uvicorn app.main:app --host 0.0.0.0 --port 8010`，不要多 worker。
2. **反向代理**：Nginx 代理 `/api` 时必须关闭缓冲，否则 SSE 会被攒包：
   ```nginx
   location /api/ {
     proxy_pass http://127.0.0.1:8010;
     proxy_http_version 1.1;
     proxy_set_header Connection "";
     proxy_buffering off;
     proxy_read_timeout 300s;
   }
   ```
3. **静态前端**：`cd web && npm run build`，将 `web/dist/` 交给 Nginx；同时把 `/api` 反代到后端。
4. **鉴权与 HTTPS**：问答与知识运营链路（`/api`）默认无鉴权，公网部署请在反向代理层增加登录、IP 白名单或 API Key；`/api/v1` 可设置 `INDOCQA_AUTH_TOKEN` 启用静态 Bearer 鉴权（本机回环与 `/api/v1/health` 豁免），生产仍建议叠加 HTTPS 与代理层防护。
5. **密钥管理**：`server/.env` 已被 `.gitignore` 忽略，切勿提交真实 Key。
6. **备份**：定期备份 `server/data/chroma/`、`server/data/db/sessions.db`、`server/data/uploads/` 与 `server/.env`。
7. **资源监控**：关注首 Token 时延、检索 P95、进程 RSS 与 `42901` 比例；基准可用 `eval/perf_benchmark.py` 复现。

## 8. 常见问题排查

| 现象 | 可能原因 | 处理 |
| --- | --- | --- |
| 前端页面打开但接口 502/500 | 后端未启动或端口不一致 | 确认 `8010` 已监听；前端 `VITE_API_TARGET` 指向正确地址 |
| `40903 ChromaBusyError` / 启动报写锁 | 有旧的 uvicorn 进程占用 `data/.chroma-write.lock` | 结束旧进程：`Get-Process python \| Stop-Process`（Windows）或 `pkill -f uvicorn`，再重启 |
| `40902 向量库与嵌入后端不一致` | 切换了 `INDOCQA_EMBED_BACKEND` 或模型 | 删除 `server/data/chroma/` 或 `--rebuild` 重建 |
| 知识库问答返回“资料未覆盖” | 未入库、目录范围过滤、弱命中闸门 | 检查 `/api/health` 的文档数；清空检索范围；确认文档已入库 |
| 直接对话不可用 | 未配置 LLM | 填写 `INDOCQA_LLM_*` 并重启后端 |
| 回答长时间无输出 | 上游模型慢/限流 | 前端 45s 会提示；后端 30s 空闲超时自动切换下一个候选模型；换更快 profile |
| `42901` 频繁 | 所有候选模型均限流或检索超时 | 增加扩展 profile；调低 `INDOCQA_RETRIEVAL_TIMEOUT` 或错峰 |
| 上传报 40003 | 文件格式不支持 | 仅支持 PDF / MD / TXT |
| 上传报 40002 | 文件超过 20 MB | 拆分或压缩文档 |
| Ollama 嵌入超时 | Ollama 未启动或模型未拉取 | `ollama serve`、`ollama pull bge-m3`；或切换 `INDOCQA_EMBED_BACKEND=local` |
| 前端搜索结果/目录树异常 | 浏览器缓存旧版本 | 硬刷新（Ctrl+F5），确认 `npm run dev` 已重启 |

## 9. 部署后自检

```bash
# 健康检查
curl http://127.0.0.1:8010/api/health

# 模型列表（不包含密钥）
curl http://127.0.0.1:8010/api/models

# 流式问答（应看到 event: trace / sources / token / done）
curl -N -X POST http://127.0.0.1:8010/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"退款一般多久到账？","mode":"kb"}'

# 后端测试
cd server && uv run pytest -q

# 前端构建
cd web && npm run lint && npm run build
```

## 10. 性能基准复现

```bash
# 后端与前端已启动
uv run --project server --with matplotlib --with psutil python eval/perf_benchmark.py
```

输出（本地生成，不入库）：

- `docs/performance/性能测试报告.md`
- `docs/performance/data/*.json|csv`
- `docs/performance/charts/*.png|svg`

报告包含检索质量、首 Token/端到端时延、入库吞吐、并发 QPS 与资源占用，可用于容量规划与回归对比。

## 11. 控制面与构建编排（`/api/v1`）部署与 CLI

问答与知识运营链路（`/api`）保持不变；控制面与构建编排链路以 `/api/v1` 提供控制面、Build、Pack 与检索能力。两者共享同一进程，但使用独立数据目录。

### 11.1 目录与配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `INDOCQA_ENGINE_DATA_DIR` | `data/engine` | 控制面 SQLite、Chroma、BM25、对象存储根目录 |
| `INDOCQA_PACKS_DIR` | `packs` | Domain Pack 根目录 |
| `INDOCQA_ENGINE_EMBED_DIMENSION` | `1024` | 构建链路嵌入维度，必须与 Ollama 模型一致 |
| `INDOCQA_LEGACY_PACK` | 空 | 问答与知识运营链路的默认 Pack，格式 `<id>@<version>` |
| `INDOCQA_LEGACY_KB` | `kb_default` | 请求未显式传 `kbId` 时用于 Pack 作用域解析 |
| `INDOCQA_ANSWER_BACKEND` | `auto` | `auto`=有 `kbId` 且 KB 有 active Build 时走构建链路；`legacy` / `engine` 分别强制问答链路与构建链路 |
| `INDOCQA_AUTH_TOKEN` | 空 | 为空不鉴权；设置后 `/api/v1` 要求 `Authorization: Bearer <token>`（`/api/v1/health` 与本机回环地址豁免） |
| `INDOCQA_FOLDER_MIGRATION_ENABLED` | `false` | 启动时是否按 Pack 迁移存量目录 |

> 单进程约束同样适用于构建链路：控制面 SQLite、Chroma、BM25 侧车与对象存储均为进程内资源，**只允许 1 个 uvicorn worker**。多实例部署必须改为外部向量库/数据库，或按 KB 分片部署。

### 11.2 CLI 快速路径

```bash
cd server
uv sync --extra dev

# 1) 初始化项目（在当前目录生成 indocqa.json 与控制面）
uv run python -m app.cli init --dir ./demo --name "示例知识库" \
  --pack example-minimal@1.0.0

# 2) 放入语料后校验配置
uv run python -m app.cli validate --dir ./demo

# 3) 构建并激活 Build
uv run python -m app.cli build --dir ./demo

# 4) 单次检索
uv run python -m app.cli query --dir ./demo "示例问题"
```

CLI 使用 Ollama 嵌入（`bge-m3`），需先执行 `ollama serve` 与 `ollama pull bge-m3`。`build` 支持 PDF / Markdown / TXT，按小节切块并写入控制面、向量与关键词索引；`query` 输出命中块、来源文档、章节与页码。

### 11.3 API 路径

```bash
# 健康与版本
curl http://127.0.0.1:8010/api/v1/health

# 安装 Pack、创建 KB、自动入库并激活 Build
curl -X POST http://127.0.0.1:8010/api/v1/packs/install \
  -H 'Content-Type: application/json' \
  -d '{"packId":"customer-service-faq","version":"1.0.0"}'
curl -X POST http://127.0.0.1:8010/api/v1/kbs \
  -H 'Content-Type: application/json' \
  -d '{"kbId":"kb_cs","name":"客服库","domainPackId":"customer-service-faq","domainPackVersion":"1.0.0"}'
curl -X POST http://127.0.0.1:8010/api/v1/kbs/kb_cs/builds \
  -H 'Content-Type: application/json' \
  -d '{"preset":"balanced@1","corpusDir":"corpus","activate":true}'

# 按 KB 问答与 UI Profile
curl -N -X POST http://127.0.0.1:8010/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"退款一般多久到账？","mode":"kb","kbId":"kb_cs"}'
curl 'http://127.0.0.1:8010/api/v1/ui-profile?kbId=kb_cs'
```

`corpusDir` 相对数据根目录（与 `INDOCQA_CORPUS_DIR` 的父目录一致）或绝对路径，且必须位于数据根目录内。

完整端点与错误码见 `docs/API.md` 第 11 节。

### 11.4 部署后自检

```bash
curl http://127.0.0.1:8010/api/health
curl http://127.0.0.1:8010/api/v1/health
curl http://127.0.0.1:8010/api/v1/ui-profile
curl http://127.0.0.1:8010/api/v1/packs
```

### 11.5 CI 门禁

`.github/workflows/ci.yml` 在每次 push / PR 执行：

- `ruff check`；
- 契约快照：`test_api_v1.py`、`test_chat_api.py`、`test_config_model.py`；
- 领域词与 Pack 资产：`test_domain_leakage.py`、`test_packs.py`；
- 全量 `pytest`；
- CLI `--help` 冒烟；
- 前端 `npm run lint` / `npm run build`。

PR 必须全绿方可合并。
