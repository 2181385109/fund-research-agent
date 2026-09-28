#!/bin/bash
# fund_data 导入账号（ADR-023）：只有 fund_data 的读写与建表权限，仅供 data-pipeline 导入使用。
# 首次初始化时由官方 entrypoint 自动执行；已有数据卷时用 root 手动执行一次（幂等，见 docs/SETUP.md）：
#   docker exec fra-mysql bash /docker-entrypoint-initdb.d/02-fund-loader.sh
# 任何应用代码都不使用 root；root 只用于这里的初始化与运维。
set -euo pipefail

: "${FUND_DATA_DB:?}" "${FUND_LOADER_USER:?}" "${FUND_LOADER_PASSWORD:?}"

# 手动执行时服务器已在监听 socket；entrypoint 执行时是临时 skip-networking 服务器，同样走 socket
mysql --protocol=socket -uroot -p"${MYSQL_ROOT_PASSWORD}" <<SQL
CREATE USER IF NOT EXISTS '${FUND_LOADER_USER}'@'%' IDENTIFIED BY '${FUND_LOADER_PASSWORD}';
ALTER USER '${FUND_LOADER_USER}'@'%' IDENTIFIED BY '${FUND_LOADER_PASSWORD}';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, REFERENCES
  ON \`${FUND_DATA_DB}\`.* TO '${FUND_LOADER_USER}'@'%';
FLUSH PRIVILEGES;
SQL

echo "[init] user ${FUND_LOADER_USER} granted read/write/DDL on ${FUND_DATA_DB}"
