# py-spy：ai-service 在 D 场景（语义缓存命中）64 并发下的采样

来源：`ai_raw.txt.gz`（折叠栈，40 s，100 Hz 目标采样率）、`ai_flame.svg`（火焰图，随后 40 s）；压测运行 `reports/perf/*_D_pyspy_profile`（D，64 并发，窗口 170 s，**py-spy 与负载同时运行，会拖慢被测进程**；样本数是近似值）。

**线程组**（总样本 7949）

| 占比 | 样本 | 名称 |
|---|---|---|
| 69.8% | 5549 | `uvloop` |
| 28.5% | 2266 | `MainThread` |
| 1.7% | 134 | `Thread-1 (_poll_wrapper)` |

**自身时间 Top（栈顶函数）**（总样本 7949）

| 占比 | 样本 | 名称 |
|---|---|---|
| 18.9% | 1499 | `run (asyncio/runners.py:118)` |
| 13.7% | 1092 | `convert (torch/nn/modules/module.py:1377)` |
| 13.2% | 1052 | `forward (torch/nn/modules/linear.py:134)` |
| 3.6% | 283 | `_worker (concurrent/futures/thread.py:90)` |
| 3.4% | 268 | `layer_norm (torch/nn/functional.py:2994)` |
| 2.9% | 230 | `compute_should_use_set_data (torch/nn/modules/module.py:941)` |
| 2.8% | 224 | `_call_impl (torch/nn/modules/module.py:1794)` |
| 2.0% | 160 | `forward (transformers/models/bert/modeling_bert.py:350)` |
| 2.0% | 160 | `forward (transformers/models/bert/modeling_bert.py:176)` |
| 1.9% | 155 | `sdpa_attention_forward (transformers/integrations/sdpa_attention.py:158)` |
| 1.8% | 146 | `forward (transformers/models/bert/modeling_bert.py:292)` |
| 1.8% | 146 | `forward (transformers/models/bert/modeling_bert.py:175)` |
| 1.8% | 145 | `forward (transformers/activations.py:89)` |
| 1.8% | 140 | `forward (transformers/models/bert/modeling_bert.py:177)` |

**包含时间 Top（栈中出现的函数）**（总样本 7949）

| 占比 | 样本 | 名称 |
|---|---|---|
| 71.5% | 5683 | `_bootstrap (threading.py:1032)` |
| 71.5% | 5683 | `_bootstrap_inner (threading.py:1075)` |
| 71.5% | 5683 | `run (threading.py:1012)` |
| 66.2% | 5264 | `_worker (concurrent/futures/thread.py:93)` |
| 66.2% | 5262 | `run (concurrent/futures/thread.py:59)` |
| 66.2% | 5259 | `decorate_context (torch/utils/_contextlib.py:124)` |
| 66.2% | 5259 | `wrapper (sentence_transformers/util/decorators.py:46)` |
| 66.2% | 5259 | `embed_documents (fund_ai/embedding/bge.py:66)` |
| 43.8% | 3485 | `encode (sentence_transformers/sentence_transformer/model.py:941)` |
| 43.8% | 3484 | `_wrapped_call_impl (torch/nn/modules/module.py:1783)` |
| 43.8% | 3484 | `_call_impl (torch/nn/modules/module.py:1794)` |
| 43.8% | 3483 | `forward (sentence_transformers/base/model.py:569)` |
| 39.9% | 3171 | `forward (sentence_transformers/base/modules/transformer.py:1650)` |
| 39.9% | 3171 | `wrapper (transformers/utils/generic.py:1068)` |
