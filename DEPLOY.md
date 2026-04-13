# Polymarket 自动交易系统 - Docker 部署指南

## 快速部署

### 1. 准备文件

将以下文件上传到 Linux 服务器：
- 整个项目文件夹
- 或者只需要：`Dockerfile`, `docker-compose.yml`, `requirements.txt`, 所有 `.py` 文件, `templates/`, `static/`

### 2. 配置环境变量

编辑 `.env` 文件：

```bash
PRIVATE_KEY=你的私钥
FUNDER_ADDRESS=你的地址
WEB_PASSWORD=设置访问密码（留空则无需密码）
MONITOR_INTERVAL=30
```

### 3. 启动服务

```bash
# 构建并启动
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down
```

### 4. 访问

浏览器打开：`http://服务器IP:5000`

如果设置了 `WEB_PASSWORD`，需要先输入密码登录。

## 数据持久化

数据库文件保存在 `./data/polymarket.db`，即使容器重启数据也不会丢失。

## 更新代码

```bash
# 停止容器
docker-compose down

# 重新构建
docker-compose build

# 启动
docker-compose up -d
```

## 安全建议

1. **必须设置 WEB_PASSWORD**，防止未授权访问
2. 使用 Nginx 反向代理 + HTTPS
3. 限制防火墙只允许特定 IP 访问 5000 端口
4. 定期备份 `./data/` 目录

## Nginx 反向代理示例

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## 故障排查

```bash
# 查看容器状态
docker ps

# 查看日志
docker-compose logs -f polymarket

# 进入容器
docker exec -it polymarket-auto-trade bash

# 重启容器
docker-compose restart
```
