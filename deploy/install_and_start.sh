#!/usr/bin/env bash
set -euo pipefail

# 车研数合（CrossBorderPlatform）Linux 一键私有化部署与健康核验脚本
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

echo "[1/4] 检查容器运行环境..."
command -v docker >/dev/null 2>&1 || { echo "未检测到 docker，请先安装 Docker Engine 24+"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "未检测到 docker compose v2 插件"; exit 1; }

echo "[2/4] 检查私有化环境配置文件..."
if [[ ! -f ".env" ]]; then
  cp .env.prod.example .env
  RAND_PG="$(openssl rand -hex 16)"
  RAND_RT="$(openssl rand -hex 16)"
  RAND_JWT="$(openssl rand -hex 32)"
  RAND_SSO="$(openssl rand -hex 32)"
  sed -i "s/CHANGE_ME_STRONG_PG_SUPER_PASSWORD/${RAND_PG}/g" .env
  sed -i "s/CHANGE_ME_STRONG_PG_RUNTIME_PASSWORD/${RAND_RT}/g" .env
  sed -i "s/CHANGE_ME_AT_LEAST_32_CHARS_RANDOM_HEX_SECRET/${RAND_JWT}/g" .env
  sed -i "s/CHANGE_ME_AT_LEAST_32_CHARS_PORTAL_SSO_SECRET/${RAND_SSO}/g" .env
  echo "已基于模板自动初始化 deploy/.env 随机数据库、会话与门户单点对接密钥（如需接入大模型请在 deploy/.env 填写 CYSH_LLM_API_KEY）"
fi

echo "[3/4] 构建并启动数据库、迁移、后端接口、后台巡检及双端门户服务..."
docker compose --env-file .env -f docker-compose.yml up -d --build

HTTP_PORT="$(grep -E '^CYSH_HTTP_PORT=' .env | cut -d'=' -f2 || echo 18080)"
HTTP_PORT="${HTTP_PORT:-18080}"

echo "[4/4] 等待网关与健康检查就绪..."
for i in {1..30}; do
  if curl -fsS "http://127.0.0.1:${HTTP_PORT}/api/health" >/dev/null 2>&1; then
    echo "私有化部署完成！企业用户端：http://127.0.0.1:${HTTP_PORT}/  运营管理端：http://127.0.0.1:${HTTP_PORT}/admin/"
    echo "首次洁净空库部署请执行以下命令创建首个平台超级管理员（将口令与邮箱替换为企业实际管理员信息）："
    echo "  docker compose --env-file .env -f docker-compose.yml exec -e CYSH_ADMIN_PASSWORD='<强密码>' api python -m app.cli create-platform-admin --email admin@company.com --name '平台管理员'"
    exit 0
  fi
  sleep 2
done

echo "服务仍在初始化中，请执行 docker compose -f deploy/docker-compose.yml ps 查看状态"
exit 1
