# HTTP Server

`agent-cook serve` 运行一个 FastAPI 服务进程。客户端提交任务后取得 Run ID。后台按并发上限执行，并把排队状态、附件和事件存入数据目录的 SQLite，产物存入同一目录的 `artifacts/`。

## 启动

用 `./cook setup` 准备环境，在 `agent-cook.json` 填写 `api_keys.openrouter`、`models.chat` 和独立的 `server.token`。也可在同一文件配置 Exa、E2B、生图模型和服务默认值。[配置示例](../agent-cook.example.json)

```bash
./cook serve --host 127.0.0.1 --port 8000 --concurrency 2
```

加入 `--sandbox docker` 或 `--sandbox e2b` 可接收文件、运行脚本和交付产物。用 `server.data_dir` 或 `--data-dir` 指定持久化目录。每个数据目录由一个服务进程使用。用 `--config` 指定配置文件，或用 `--request` 加载部署者维护的任务和扩展选项。请求里的任务、附件 ID 与模型选项通过 HTTP 提交。

## API

请求携带 `Authorization: Bearer <service-token>`。部署服务时由反向代理提供 HTTPS，并允许 SSE 流式响应。

| 接口 | 用途 |
|---|---|
| `GET /healthz` | 检查进程和认证 |
| `GET /v1/models` | 查询可选的对话和生图模型 |
| `POST /v1/files?name=sales.csv` | 请求体是文件原始字节。返回 `file_id` |
| `POST /v1/runs` | JSON 包含 `task`、可选 `file_ids`、`model` 和 `image_model`。返回 `202` 以及 `run_id` |
| `GET /v1/runs/{id}` | 查询任务状态、答复和产物 |
| `GET /v1/runs/{id}/events` | 获取 SSE 事件流。设置 `Last-Event-ID` 从指定序号之后续接 |
| `POST /v1/runs/{id}/cancel` | 请求取消排队中或执行中的任务 |
| `GET /v1/runs/{id}/artifacts/{artifact_id}` | 下载产物 |

`POST /v1/runs` 可带 `Idempotency-Key`。相同请求可重试并读到同一 Run ID。断开 SSE 后任务继续执行。服务重启会恢复排队任务，之前执行中的任务会标记为 `interrupted`。

## 容器部署

在仓库根目录构建服务镜像。使用命名卷保留数据。容器可读取 `/data/agent-cook.json`，把文件提供给容器用户即可。也可以复制 `.env.example` 为 `.env`、填入配置，再用下面的 `--env-file` 启动。

```bash
docker build -f agent/Dockerfile.server -t agent-cook-server:local agent
docker run --rm --env-file .env -p 127.0.0.1:8000:8000 \
  -v agent-cook-data:/data agent-cook-server:local --sandbox none
```

服务镜像也支持配置 `E2B_API_KEY` 和 `E2B_TEMPLATE` 后传入 `--sandbox e2b`。使用宿主 Docker 作为任务后端时，可在宿主机上运行 `./cook serve --sandbox docker`，并用 `./cook setup --docker` 准备任务镜像。
