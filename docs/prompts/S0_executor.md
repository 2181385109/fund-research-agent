你是 fund-research-agent（基金投研助手）项目的执行者，项目目录是 D:\xiangmu\fund-research-agent。本轮只做 S0「脚手架与基础设施」。

开工前按顺序读三份文件：CLAUDE.md（项目约定，必须遵守）、docs/PLAN.md（重点是 §0–§4 和 §5 的 S0）、docs/PROGRESS.md。验收标准以 PLAN.md 为准，你不能修改它；认为有问题就写进 PROGRESS.md 的「给统筹的问题」。

【第 0 步：环境准备（这两件事用户已授权你来做）】

0-1 给 WSL2 设内存上限
- 先读 %USERPROFILE%\.wslconfig，里面已经有 ticket-qa 项目加的 vmIdleTimeout 和注释，必须原样保留。只在 [wsl2] 段下追加 memory=10GB 和 swap=4GB，并附一行注释说明用途。
- 运行 wsl --shutdown。注意：这会停掉 WSL 里所有容器，包括 ticket-qa 的。
- 然后运行 wscript.exe D:\tools\wsl-keepalive.vbs，把常驻会话拉起来。
- 验证：wsl -l -v 显示 Running；WSL 内运行 free -h，总内存约 10G。两段输出原文都贴进 PROGRESS。
- ticket-qa 的容器设置了 restart: unless-stopped，会随 WSL 自动恢复并占用内存。不要自己停它们：在最终报告里告诉用户它们各占多少内存，由用户决定停不停。

0-2 生成 .env
- 先建好 .gitignore（包含 .env、data/raw/、data/snapshots/），再写 .env.example，然后用脚本从 .env.example 生成 .env。
- LLM_API_KEY 的值由脚本从 D:\个人\api(Deepseek).txt 读取（35 字节，去掉首尾空白）后写入 .env。全程不打印、不回显、不写进任何日志或结果文件，也不在对话里复述 key。
- 其余密码类变量（MySQL root 密码、应用账号密码、fund_reader 密码、内部回调共享密钥等）由脚本随机生成后写入 .env。
- 用 git check-ignore -v .env 证明 .env 已被忽略，输出贴进 PROGRESS。

【S0 交付物】按 PLAN S0 的清单逐项完成，下面是补充要求。

1. 仓库
- git init、.gitattributes（* text=auto eol=lf）、MIT LICENSE（作者 YaoYinJie）。
- README 骨架：一句话介绍、架构图（先从 PLAN §1 复制过来）、当前进度，以及两句固定声明：「数据仅用于学习研究」「本项目输出不构成投资建议」。
- 目录结构严格按 CLAUDE.md §3。还没实现的模块放一个 README 占位，说明它属于哪个阶段。

2. docker-compose.yml（默认只起 infra）
- 镜像一律固定到具体版本号，不用 latest。
- mysql 8.4：deploy/mysql/init/ 下的初始化脚本创建 fra_app、fund_data 两个库。应用账号只有 fra_app 的权限；fund_reader 在 fund_data 上只有 SELECT。
- redis 8.x：确认镜像自带 Query Engine（例如用 MODULE LIST 或 FT._LIST 验证），结果记入 ADR。
- elasticsearch 8.x + IK：deploy/elasticsearch/Dockerfile 自建镜像，ES_JAVA_OPTS=-Xms1g -Xmx1g，单节点，本地开发可以关闭安全认证（写 ADR）。
  - 先确认所选 ES 版本有对应的 IK 发布包，并且从本机能下载到（infinilabs 发布站或 GitHub）。
  - 真装不上时按 PLAN 用 analysis-smartcn 兜底，登记 ADR 后继续，不用停。
- milvus 2.5.x standalone：内嵌 etcd + 本地存储（参考官方 standalone_embed 脚本的环境变量和 embedEtcd.yaml），单容器。
- 每个服务都要有 mem_limit（按 PLAN §3）、healthcheck、具名卷、restart: unless-stopped。宿主机端口从 .env 读，默认值按 PLAN §2.2。
- 拉镜像走 WSL 里已配置的镜像加速；某个镜像拉不下来时把报错原文记下来。

3. backend（Java）
- 用 JDK 17（D:\tools\jdk-17）、Maven（D:\tools\maven）、Spring Boot 3.5.x，groupId 是 com.fundagent。
- GET /api/health：检查 MySQL 和 Redis，每项给出 UP/DOWN 和耗时；任一 DOWN 就返回 503。
- Flyway 基线迁移；统一响应体 ApiResponse<T> 和全局异常处理先搭好。
- 至少 1 个单测（例如 health 在依赖 DOWN 时返回 503，依赖用 Mockito mock）。

4. Python 三个包（ai-service、mcp-tools、data-pipeline）
- 都用 pyproject.toml 和 Python 3.12，配置统一走 pydantic-settings。venv 策略（每包一个还是共用根 .venv）由你决定，写 ADR。
- ai-service：FastAPI，GET /health 检查 Milvus、ES、Redis（带超时，任一 DOWN 返回 503），至少 1 个单测（依赖用 fake）。
- mcp-tools、data-pipeline：只搭骨架，各 1 个单测。本阶段不写任何业务逻辑。

5. scripts/llm_smoke.py
- 先调 GET {LLM_BASE_URL}/models 列出可用模型。
- 注意：另一个项目的记录显示，deepseek-chat / deepseek-reasoner 这两个请求名在 2026-07 已被官方弃用，响应里的 model 字段会显示别的名字。所以请以 /models 的结果和官方文档为准，选一个支持 tool calling 和流式输出的通用对话模型作为 LLM_MODEL 的默认值，登记 ADR。
- 发两次请求：
  - 一次流式对话，记录首 token 延迟和总耗时；
  - 一次 tool call：给一个假的 get_latest_nav(share_code) 工具定义，问「110022 最新净值是多少」，断言响应里有 tool_calls，并且参数里有 share_code。
- 打印并保存：请求的模型名、响应里的 model 字段、usage、耗时。结果写到 reports/smoke/<UTC时间戳>/summary.json，字段按 PLAN §4.4 能填的都填，绝不包含 key。
- 调用失败就停下来报告，不要反复重试、烧额度。

6. scripts/security_scan.py
- 扫描 git ls-files 列出的已跟踪文件，检查：API key 模式（如 sk- 开头的长串）、本机绝对路径（D:\、E:\python、C:\Users 等，文档里写环境说明的地方可以加豁免，但要在脚本里写明理由）、.env 文件、*.pdf、data/raw 和 data/snapshots 下的文件。
- 加 --history 选项，用于扫描全部提交历史。
- 发现问题时返回非 0 退出码。
- 给它写单测，用临时目录造违规样例。

7. CI（.github/workflows/ci.yml）
- backend 用 temurin 17 跑 mvn -B verify。
- 三个 Python 包都跑 ruff check、ruff format --check 和 pytest -m "not integration and not slow and not live"。
- 配好 Maven 和 pip 缓存。CI 不下载模型、不调用 LLM、不访问外网。

8. 文档
- docs/SETUP.md：前置条件、.wslconfig 设置（写明为什么是 10GB）、如何经 wsl 启动 compose（给出可以直接复制的命令）、端口表、排障（WSL 被停时如何用 keepalive 恢复；与 ticket-qa 共存时的内存建议）。
- docs/DECISIONS.md：把 PLAN §2 的选型登记为 ADR-001 起的条目，再加上本阶段你自己做的决定。

9. GitHub
- 首次 push 之前必须同时跑 security_scan.py 和 security_scan.py --history，都通过后再推。
- 用 "C:\Program Files\GitHub CLI\gh.exe" 创建公开仓库 2181385109/fund-research-agent，描述写「基金投研助手：RAG + LangGraph Agent + MCP，Java/Python 双栈（学习项目，不构成投资建议）」，然后 push main。

【验收（逐条在 PROGRESS 中给出证据原文）】
1. 4 个 infra 服务 healthy：贴 docker compose ps 原文。
2. docker stats --no-stream 原文，并与 PLAN §3 的预算逐项对照（ticket-qa 的容器如果在跑，单独列出）。
3. 对「基金管理人的管理费率」调用 IK _analyze，ik_max_word 和 ik_smart 各一次，贴原文。如果退回了 smartcn，贴 smartcn 的结果并注明。
4. 用 fund_reader 执行 INSERT 被拒，SELECT 可以执行：贴原文。
5. 两个 health 接口依赖全部 UP：贴原文。再停掉 redis 容器，确认 backend 返回 503，贴原文，然后恢复 redis。
6. llm_smoke 的流式和 tool call 都成功：给出请求模型名、响应模型名、首 token 延迟，以及 summary.json 路径。
7. 首次 CI 全绿（贴 run 链接）；安全扫描的两种模式都通过（贴原文）。
8. 第 0 步的证据：wsl -l -v、free -h、git check-ignore -v .env 的输出原文。

【本轮不做】
不写 S1 及以后的任何内容：不筛基金池、不调用 AkShare、不下载 PDF、不建 fund_data 的业务表、不写入库或检索代码。

【需要停下来问的情况】
- DeepSeek 调用失败。
- 某个核心镜像（MySQL、Redis、ES、Milvus）无论如何都拉不下来。
- 实测内存明显超出预算，infra 合计超过 6.5g。
- 需要偏离 PLAN 的选型（IK 退回 smartcn 除外）。
以上情况都先写进 PROGRESS 再停。

【收尾】
- 按 CLAUDE.md §11 的模板在 PROGRESS.md 追加「S0」一节，并更新顶部的「当前状态」。
- commit（小步提交，不加 Co-Authored-By）、push，确认 CI 全绿。
- 最后给用户一段简短报告：完成了什么、每条验收是否通过、实测内存和 LLM 延迟、需要用户做的事、给统筹的问题。然后停下，等统筹审查。
