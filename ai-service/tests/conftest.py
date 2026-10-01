"""测试公共设置：现有测试大量使用 create_app + TestClient，它们不应该去占用 gRPC 端口（S9）。
gRPC 的测试（test_grpc_server.py）显式构造 Settings(ai_grpc_enabled=True, ai_grpc_port=0)。"""

import os

os.environ.setdefault("AI_GRPC_ENABLED", "false")
