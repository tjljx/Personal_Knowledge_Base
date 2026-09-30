# Personal Knowledge Base / 个人知识库

一个可自行部署的文档知识库。前端使用 Vue 3、TypeScript 和 Element Plus；API 与独立任务 worker 使用 FastAPI、SQLAlchemy 和 Alembic。

目前支持账号与知识库权限、文件上传和版本管理、异步解析、关键词与可选向量检索、流式问答及原文引用、实体关系图谱和云模型预算控制。引用以当前有效版本的原文片段为准；图谱关系只提供检索线索。知识库默认禁止向云模型发送资料，管理员须逐库授权。浏览器无操作超过 10 分钟后，服务端会使会话失效。

项目处于试用阶段。自动测试和检索冒烟不能代替真实资料的人工问答验收；大型 PDF、图谱人工纠错和多跳查询仍有限制，见[当前状态](docs/STATUS.md)。

## 快速开始：Docker Compose

需要 Docker Engine 与 Compose 插件。首次启动会下载 PostgreSQL、Qdrant、Ollama 等镜像及模型，需为模型和数据预留磁盘空间。命令在仓库根目录执行：

1. 复制 `.env.selfhost.example` 为 `.env.selfhost`。生成独立随机的数据库密码和 JWT 密钥；将同一个数据库密码填入 `POSTGRES_PASSWORD` 与 `DATABASE_URL`。密码建议使用十六进制字符，避免 URL 转义问题。示例密钥不能用于实际部署。
2. 执行 `docker compose --env-file .env.selfhost -f compose.selfhost.yaml up -d --build db qdrant ollama reranker migrate api worker web`。
3. 执行 `docker compose --env-file .env.selfhost -f compose.selfhost.yaml exec ollama ollama pull qwen3-embedding:0.6b`。等待重排模型下载完成，并检查 `docker compose --env-file .env.selfhost -f compose.selfhost.yaml ps`。
4. 执行 `docker compose --env-file .env.selfhost -f compose.selfhost.yaml exec api python -m knowledge_api.cli create-admin`，交互创建管理员。
5. 打开 `http://127.0.0.1:8765/`。默认只监听本机；跨设备访问需配置 HTTPS 反向代理和访问控制，详见[部署说明](docs/DEPLOYMENT.md)。

容器数据使用命名卷保存，包括 PostgreSQL、原件、Qdrant、Ollama 模型及重排模型缓存。**不要通过 `down -v` 删除生产数据。**

## 从源码运行

需要 Python 3.12+、Node.js 24+ 和 npm。最小试用可使用 SQLite 与关键词检索；完整功能还需 PostgreSQL、Qdrant、Ollama 和本地重排服务。可选 OCR/Office 解析依赖见[部署说明](docs/DEPLOYMENT.md)。

1. 复制 `.env.example` 为 `.env`，为 `JWT_SECRET` 设置独立随机值；最小试用把 `DATABASE_URL` 改为 `sqlite+pysqlite:///./data/knowledge.db`。先创建 `data` 目录。
2. 创建虚拟环境并安装：`python -m venv .venv`，激活后执行 `python -m pip install -e "apps/api[dev]"`。
3. 设置 `PYTHONPATH=apps/api`，运行 `python -m alembic -c apps/api/alembic.ini upgrade head`，再运行 `python -m knowledge_api.cli create-admin`。
4. 分别运行 `python -m uvicorn knowledge_api.main:app --app-dir apps/api --host 127.0.0.1 --port 8000` 和 `python -m knowledge_api.worker`。
5. 在 `apps/web` 运行 `npm ci`、`npm run dev`，打开 Vite 输出的地址。Vite 开发代理把 `/api` 转发到本机 API。

Windows PowerShell 设置变量：`$env:PYTHONPATH='apps/api'`；Linux/macOS：`export PYTHONPATH=apps/api`。SQLite 适合本机试用和测试，长期部署请用 PostgreSQL。详见[部署说明](docs/DEPLOYMENT.md)。

## 文档

- [架构与数据边界](docs/ARCHITECTURE.md)
- [Docker 与源码部署](docs/DEPLOYMENT.md)
- [当前状态与限制](docs/STATUS.md)
- [安全报告与密钥处理](SECURITY.md)
- [贡献说明](CONTRIBUTING.md)

## 开发验证

在仓库根目录：`python -m ruff check apps/api`、`python -m ruff format --check apps/api`、`python -m pytest apps/api/tests -q`；在 `apps/web`：`npm ci`、`npm run build`。测试主要使用临时 SQLite，不能证明 PostgreSQL、模型服务和真实资料问答质量已通过验收。

代码按 [Apache License 2.0](LICENSE) 发布；模型权重、基础镜像和第三方依赖分别遵循各自许可证。本仓库不包含用户资料、模型权重或云模型密钥。
