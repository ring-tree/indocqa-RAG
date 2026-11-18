# 贡献指南

感谢参与 IndocQA。本文说明开发环境、代码规范、测试要求与提交流程。

---

## 1. 开发环境

### 1.1 依赖

| 依赖 | 版本 |
| --- | --- |
| Python | 3.11+（推荐 3.13） |
| Node.js | 20+ |
| uv | 最新版 |
| Ollama（可选） | 本地嵌入模型 |

### 1.2 初始化

```bash
# 后端
cd server
cp .env.example .env
uv sync --extra dev

# 前端
cd ../web
npm ci
```

### 1.3 本地运行

```bash
# 后端
cd server
uv run uvicorn app.main:app --host 127.0.0.1 --port 8010

# 前端
cd web
npm run dev
```

前端默认访问 `http://127.0.0.1:5174`。

---

## 2. 分支与提交

| 项 | 约定 |
| --- | --- |
| 分支命名 | `feat/<scope>`、`fix/<scope>`、`docs/<scope>`、`refactor/<scope>` |
| 提交信息 | 祈使句，说明“为什么改”；示例：`fix: 修正 Build 激活时的指针竞争` |
| 提交粒度 | 一个提交只做一件事；避免混合格式化与逻辑修改 |
| 合并方式 | Squash merge，保持主分支线性历史 |

提交前请确保工作区无未跟踪的运行时产物（`data/`、`docs/performance/`、`server/.env`）。

---

## 3. 代码规范

### 3.1 后端

- 行宽 100，目标 Python 3.11。
- 使用类型标注；公共函数需说明输入、输出与异常。
- 遵守分层边界：
  - 领域词汇只允许出现在 `server/packs/`；
  - `server/app/` 与 `web/src/` 不得出现行业术语；
  - 接口层不得包含业务规则或领域分支。
- 禁止在 `server/app` 中使用 `print`；使用日志。

```bash
cd server
uv run ruff check app tests
```

### 3.2 前端

- TypeScript 严格模式；组件与工具函数使用显式类型。
- 样式优先使用现有 Tailwind 与设计变量，避免新增全局样式。
- 文案不得硬编码领域示例。

```bash
cd web
npm run lint
npm run format   # 按需
npm run build
```

---

## 4. 测试要求

| 类型 | 命令 | 要求 |
| --- | --- | --- |
| 后端全量 | `cd server && uv run pytest -q` | 全部通过 |
| 后端静态检查 | `cd server && uv run ruff check app tests` | 无告警 |
| 领域词守卫 | `uv run pytest tests/test_domain_leakage.py -q` | 命中 0 |
| 契约快照 | `uv run pytest tests/test_api_v1.py tests/test_chat_api.py tests/test_config_model.py -q` | 全部通过 |
| 前端 | `cd web && npm run lint && npm run build` | 无错误 |

新增行为需补充测试；修复缺陷需提供可复现用例。不得通过删除用例、添加 `skip` 或放宽断言来通过测试。

---

## 5. 新增 Domain Pack

1. 复制 `server/packs/example-minimal/1.0.0/` 作为模板。
2. 填写 `manifest.json`，声明 `id`、`version`、`domain` 与 `engine_range`。
3. 按需补充 `terms / parser / folders / classification / intent / data-facts / authority / persona / prompts / ui / eval`。
4. 运行校验：

   ```bash
   cd server
   uv run python -m app.cli validate --dir <项目目录>
   ```

5. 绑定 KB 并创建 Build，验证问答与引用。
6. 运行领域词守卫与评测，确认引擎目录无领域词汇。

详细字段说明见 [PACK_GUIDE.md](PACK_GUIDE.md)。

---

## 6. 文档要求

- API 变更同步更新 `docs/API.md`。
- 配置、部署或数据目录变更同步更新 `docs/DEPLOYMENT.md`。
- 架构或模块职责变更同步更新 `docs/ARCHITECTURE.md`。
- 用户可见行为变更同步更新 `docs/USAGE.md`。
- README 只保留定位、特性、快速开始与导航，细节放入 `docs/`。

---

## 7. 提交前检查清单

- [ ] 后端 `pytest` 全部通过
- [ ] 后端 `ruff` 无告警
- [ ] 领域词守卫命中为 0
- [ ] 前端 `lint` 与 `build` 通过
- [ ] 新增或修改行为有对应测试
- [ ] 相关文档已同步
- [ ] 未提交 `.env`、运行时数据、临时文件或生成报告
- [ ] 提交信息说明变更原因

---

## 8. 问题反馈

提交 Issue 时请包含：

1. 复现步骤与环境（操作系统、Python/Node 版本、嵌入后端）；
2. 期望行为与实际行为；
3. 相关日志、`X-Request-Id` 或错误码；
4. 如涉及检索质量，请附问题、命中文档与预期来源。
