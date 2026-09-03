# 05 — 收口 start.sh、配置和用户可见文档

**构建内容：** 让部署者通过保留的 `start.sh` 启动一个命名一致、配置说明准确的 OpenAI Responses Proxy，并能从根路径、健康检查、CLI help 和 README 看清真实能力与限制。

**受阻于：** 04 — contract：删除旧 Chat/Claude 产品路径。

## 覆盖范围

- Requirements：`REQ-19`、`REQ-20`、`REQ-29`、`REQ-30`、`REQ-31`、`REQ-32`、`REQ-33`、`REQ-34`、`REQ-35`。
- Scenarios：`SCN-22`、`SCN-23`、`SCN-24`、`SCN-25`、`SCN-26`、`SCN-32`。
- 本 slice 收口运行配置、启动契约、项目元数据和文档，不改变已完成的代理协议行为。

## Acceptance Criteria

- [ ] `start.sh` 保持可执行，能从任意 cwd 定位仓库并运行项目虚拟环境中的应用。
- [ ] `.venv` 缺失或不完整时可创建/修复；完整环境不会重复 sync；所有 CLI 参数原样传递。
- [ ] `.env` 继续由 python-dotenv 加载，Shell 不 source 它，文档明确修改后需重启。
- [ ] Base URL 必填契约、普通密钥回退、代理密钥、目录行为和 7072 端口在 README 与 `.env.example` 一致。
- [ ] 根路径、健康检查、FastAPI title 和 CLI help 只描述 Responses；健康检查不声称真实目录或模型成功。
- [ ] distribution 与 console script 改名为 openai-responses-proxy，lockfile 同步，旧命令退出该分支。
- [ ] README 提供可复制的非流式与流式 `/v1/responses` 示例，但不包含真实密钥。
- [ ] 不新增 Python 依赖，完整自动验证保持通过。

## 验证方式

- 使用 `$tdd` 先扩充隔离 shell 测试，再调整启动/配置/文案。
- 运行 shell 语法检查、启动脚本测试和 `./start.sh --help`。
- 从仓库外 cwd 执行 help 验证路径定位。
- 运行 lockfile 一致性检查、完整 Python suite 和编译检查。
- 搜索旧产品名、旧路径、8000 错误端口和真实凭据。

## 执行约束

- `start.sh` 继续只负责环境准备与 `exec python -m src.main`，不得自行解析 `.env`。
- 不增加新依赖来解决已有库能够完成的问题。
- 外部环境变量名保持兼容，内部命名使用 upstream/Responses 语义。
- 所有新文档示例使用占位值或环境变量，不写入本地 secret。

## 范围之外

- 修改 Responses 转发语义。
- 自动迁移其他服务的旧 Chat endpoint 配置。
- push 分支或发布包。
