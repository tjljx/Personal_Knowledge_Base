# 贡献指南

提交改动前请先阅读 [架构说明](docs/ARCHITECTURE.md)和[当前状态](docs/STATUS.md)。保持知识库权限、当前文档版本、原文引用和云端授权边界一致。数据库结构变更需包含 Alembic 迁移。不要提交真实资料、`.env`、运行数据或模型权重。

后端运行 `python -m ruff check apps/api`、`python -m ruff format --check apps/api` 和 `python -m pytest apps/api/tests -q`；前端在 `apps/web` 运行 `npm ci` 与 `npm run build`。若改动检索或解析，请附上可复现的匿名测试资料和结果，不要用自动冒烟代替人工答案质量验收。

提交 issue 时描述复现步骤、预期结果和去标识化后的错误信息。感谢贡献。
