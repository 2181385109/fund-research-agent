#!/bin/bash
# MySQL 首次初始化（仅在数据卷为空时由官方 entrypoint 执行一次）。
# 建两个库和两个账号；密码来自容器环境变量（compose 从 .env 注入），不写死在仓库里。
#   fra_app      业务库；应用账号 ${MYSQL_APP_USER} 只有这个库的权限
#   fund_data    基金数据快照库；只读账号 ${FUND_READER_USER} 只有 SELECT
set -euo pipefail

: "${MYSQL_APP_DB:?}" "${MYSQL_APP_USER:?}" "${MYSQL_APP_PASSWORD:?}"
: "${FUND_DATA_DB:?}" "${FUND_READER_USER:?}" "${FUND_READER_PASSWORD:?}"

mysql --protocol=socket -uroot -p"${MYSQL_ROOT_PASSWORD}" <<SQL
CREATE DATABASE IF NOT EXISTS \`${MYSQL_APP_DB}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS \`${FUND_DATA_DB}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE USER IF NOT EXISTS '${MYSQL_APP_USER}'@'%' IDENTIFIED BY '${MYSQL_APP_PASSWORD}';
GRANT ALL PRIVILEGES ON \`${MYSQL_APP_DB}\`.* TO '${MYSQL_APP_USER}'@'%';

CREATE USER IF NOT EXISTS '${FUND_READER_USER}'@'%' IDENTIFIED BY '${FUND_READER_PASSWORD}';
GRANT SELECT ON \`${FUND_DATA_DB}\`.* TO '${FUND_READER_USER}'@'%';

FLUSH PRIVILEGES;
SQL

echo "[init] databases ${MYSQL_APP_DB}, ${FUND_DATA_DB}; users ${MYSQL_APP_USER}, ${FUND_READER_USER} created"
