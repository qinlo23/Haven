# Docker 服务

| 服务 | 用途 | 访问地址 |
| --- | --- | --- |
| Neo4j | 图数据库 | Browser：`http://localhost:7474`；Bolt：`bolt://localhost:7687` |
| Neo4j MCP | 通过 MCP 查询和写入 Neo4j | `http://localhost:8000/mcp/` |
| Qdrant | 向量数据库 | Dashboard：`http://localhost:6333/dashboard`；gRPC：`localhost:6334` |

密码放在 `docker/.env` 中，当前为 `NEO4J_PASSWORD=123456`，Neo4j 用户名为 `neo4j`。
环境文件只存放密码，镜像、端口等配置直接修改各服务的 `docker-compose.yaml`。
若环境文件不存在，可复制 `docker/.env.example` 为 `docker/.env`。
已有数据库修改 `.env` 不会重置密码。

从仓库根目录执行：

```bash
# 先启动 Neo4j，等待数据库就绪后再启动 MCP
docker compose --env-file docker/.env -f docker/neo4j/docker-compose.yaml up -d --wait
docker compose --env-file docker/.env -f docker/neo4j-mcp/docker-compose.yaml up -d

# Qdrant 可独立启动
docker compose -f docker/qdrant/docker-compose.yaml up -d
```

MCP 通过共享网络 `haven-neo4j-net` 复用 Neo4j，不创建第二个数据库。
Neo4j 数据保存在 `docker/neo4j/data`，Qdrant 数据和快照保存在
`docker/qdrant/storage`、`docker/qdrant/snapshots`。

查看日志或停止服务时，将对应命令中的 `up -d`（Neo4j 包含 `--wait`）
替换为 `logs -f` 或 `down`。停止服务会保留本地数据。
