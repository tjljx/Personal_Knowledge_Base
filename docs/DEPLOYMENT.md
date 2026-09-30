# 部署说明

## Docker Compose

根目录的 `compose.selfhost.yaml` 是通用自托管配置，包含 PostgreSQL 16、Qdrant、Ollama、重排服务、迁移、API、worker 和 Nginx 前端。复制 `.env.selfhost.example` 为 `.env.selfhost`，独立生成数据库密码与 JWT 密钥。`POSTGRES_PASSWORD` 必须与 `DATABASE_URL` 中的密码一致。`.env.selfhost` 是私有文件，不要提交、上传或贴入 issue。

启动命令见 [README](../README.md)。拉取 `qwen3-embedding:0.6b` 后，查看 `docker compose --env-file .env.selfhost -f compose.selfhost.yaml ps`、API 健康接口及实际上传、解析和查询状态。首次重排模型下载可能较慢；其模型缓存保存在命名卷。

默认只在 `127.0.0.1:8765` 监听 HTTP。外网访问需在前面配置 HTTPS 反向代理、认证或访问范围控制，并按实际域名更新 `CORS_ORIGINS`。不要直接把数据库、Qdrant、Ollama 或重排端口暴露到公网。持久卷要纳入自己的备份和恢复演练；原件与 PostgreSQL 必须一起保存，Qdrant 可重建。升级前阅读变更并运行数据库迁移。

云模型为可选能力。默认示例使用无效网关地址且没有 API key；只有填入自己的供应商地址、模型、密钥和价格上限，并在界面由管理员逐库授权后才会发出资料请求。不要在公开环境开启云端功能前忽略资料外发和费用影响。

## 从源码运行

最小试用：Python 3.12+、Node.js 24+，SQLite、关键词检索；按 [README](../README.md) 设置 `.env`、迁移、管理员、API、worker、前端。最小模式不提供向量召回或本地重排；没有有效云模型配置时无法产生模型问答。SQLite 不建议多人长期使用。

完整部署：提供 PostgreSQL 16、Qdrant、Ollama 与兼容的本地重排服务；在 `.env` 设置 `DATABASE_URL`、`EMBEDDING_MODEL`、`EMBEDDING_BASE_URL`、`EMBEDDING_EXPECTED_DIMENSIONS`、`VECTOR_STORE_URL`、`RERANK_BASE_URL` 等。API 与 worker 必须使用同一套环境和可写的 `STORAGE_ROOT`。Ollama 中拉取与配置一致的嵌入模型。重排服务可由 `apps/reranker/Dockerfile` 独立构建运行；其权重首次运行时下载。

OCR 和文档转换依赖系统命令与字体：Tesseract（含中文与英文语言包）、Poppler、LibreOffice Writer、Noto CJK。缺少它们时相关格式的解析或分页能力受限。生产前端可以执行 `npm run build` 并用 Web 服务器提供 `apps/web/dist`，将 `/api/` 反向代理到 FastAPI；仓库的 `apps/web/nginx.conf` 是容器网络配置，非裸机可直接照搬的主机名配置。

任何部署方式都需要独立保管密钥、限制文件系统权限、为数据库和原件做备份，并用真实资料做权限与引用验收。
