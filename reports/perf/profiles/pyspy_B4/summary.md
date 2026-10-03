# py-spy：ai-service 在 B 场景 4 并发下的采样

来源：`ai_raw.txt.gz`（折叠栈，55 s，100 Hz 目标采样率）、`ai_flame.svg`（火焰图，随后 55 s）、`ai_dump_after.txt`（采样后的线程栈快照）；压测运行 `reports/perf/20261003T075622Z_B_pyspy_profile`（B，4 并发，窗口 230 s，**py-spy 与负载同时运行，会拖慢被测进程**；py-spy 报告「落后 1–2 s」，样本数是近似值）。

**线程组**（总样本 8558）

| 占比 | 样本 | 名称 |
|---|---|---|
| 74.6% | 6380 | `AnyIO worker thread` |
| 24.7% | 2116 | `MainThread` |
| 0.5% | 44 | `Thread-1 (_poll_wrapper)` |
| 0.2% | 16 | `uvloop` |
| 0.0% | 2 | `thread` |

**自身时间 Top（栈顶函数）**（总样本 8558）

| 占比 | 样本 | 名称 |
|---|---|---|
| 51.6% | 4418 | `forward (torch/nn/modules/linear.py:134)` |
| 6.9% | 594 | `sdpa_attention_forward (transformers/integrations/sdpa_attention.py:158)` |
| 6.3% | 542 | `run (asyncio/runners.py:118)` |
| 3.7% | 317 | `forward (transformers/activations.py:89)` |
| 3.0% | 253 | `forward (transformers/models/xlm_roberta/modeling_xlm_roberta.py:383)` |
| 2.2% | 187 | `apply_chunking_to_forward (transformers/pytorch_utils.py:199)` |
| 2.1% | 176 | `layer_norm (torch/nn/functional.py:2994)` |
| 1.3% | 114 | `create_default_context (ssl.py:707)` |
| 0.7% | 64 | `forward (transformers/models/xlm_roberta/modeling_xlm_roberta.py:397)` |
| 0.7% | 64 | `forward (transformers/models/xlm_roberta/modeling_xlm_roberta.py:339)` |
| 0.5% | 44 | `run (threading.py:1012)` |
| 0.4% | 33 | `_encode_plus (transformers/tokenization_utils_tokenizers.py:1063)` |
| 0.3% | 28 | `connect_tcp (anyio/_backends/_asyncio.py:2848)` |
| 0.3% | 25 | `_write_buffer (rich/console.py:2124)` |

**包含时间 Top（栈中出现的函数）**（总样本 8558）

| 占比 | 样本 | 名称 |
|---|---|---|
| 75.2% | 6439 | `_bootstrap_inner (threading.py:1075)` |
| 75.2% | 6439 | `_bootstrap (threading.py:1032)` |
| 74.5% | 6379 | `run (anyio/_backends/_asyncio.py:1100)` |
| 74.5% | 6379 | `work (fund_ai/mcp_server/server.py:123)` |
| 74.0% | 6330 | `wrapper (sentence_transformers/util/decorators.py:46)` |
| 74.0% | 6330 | `decorate_context (torch/utils/_contextlib.py:124)` |
| 73.4% | 6279 | `wrapper (sentence_transformers/util/decorators.py:203)` |
| 73.4% | 6279 | `retrieve (fund_ai/retrieval/service.py:242)` |
| 73.4% | 6279 | `score (fund_ai/rerank/cross_encoder.py:57)` |
| 73.3% | 6277 | `_call_impl (torch/nn/modules/module.py:1794)` |
| 73.3% | 6277 | `_wrapped_call_impl (torch/nn/modules/module.py:1783)` |
| 73.3% | 6276 | `forward (sentence_transformers/base/model.py:569)` |
| 73.3% | 6275 | `forward (sentence_transformers/base/modules/transformer.py:1650)` |
| 73.1% | 6260 | `wrapper (transformers/utils/output_capturing.py:287)` |
