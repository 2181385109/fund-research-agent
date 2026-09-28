# Elasticsearch 8 + IK 中文分词插件（ADR-005）。
# IK 发布包版本必须与 ES 版本严格一致；来源 infinilabs 官方发布站（GitHub Releases 上 8.19.x 没有对应资产，S0 实测 404）。
# IK 不可用时的兜底：把下面的 install 行换成 `bin/elasticsearch-plugin install --batch analysis-smartcn` 并登记 ADR。
ARG ES_VERSION=8.19.22
FROM elasticsearch:${ES_VERSION}
ARG ES_VERSION
ARG IK_URL=https://release.infinilabs.com/analysis-ik/stable/elasticsearch-analysis-ik-${ES_VERSION}.zip
RUN bin/elasticsearch-plugin install --batch "${IK_URL}" \
    && bin/elasticsearch-plugin list
