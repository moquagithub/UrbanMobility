# Free Inference Options — DGX Spark vs Cloud Providers

**Purpose**: compare the **zero-cost** inference options available for MobimixEte2026 —
models self-hosted on DGX Spark and online providers with a free tier — and determine
the most suitable architecture.

> ⚠️ **Status: PROJECTIONS.** Values are **calculated**, except those marked
> "measured ✅". They serve as a sizing framework, to be replaced by real benchmark
> measurements. Section 8 is provided for recording discrepancies.

**Projection date**: August 2026 — **provider quotas verified**: May 2026

---

## 1. Method

### 1.1 Calculation model (local)

Generation throughput is capped by memory bandwidth:

```
projected throughput = (bandwidth ÷ model size) × observed efficiency
```

| Parameter | Value | Source |
|---|---|---|
| DGX Spark bandwidth | 273 GB/s | GB10 specification |
| Measured throughput (Llama-3.1-8B FP16) | **14.0 tok/s** | **measured ✅** |
| Theoretical throughput | 17.1 tok/s | calculation |
| **Efficiency applied** | **82 %** | derived |

### 1.2 Model validation

Cross-checked against an independent measurement:

| Model | Projection | Actual measurement | Deviation |
|---|---|---|---|
| Qwen3-14B FP16 | 7.7 tok/s | **8.2 tok/s** ✅ | **+6 %** |

A 6 % deviation on a very different model size validates the method. Local projections
are reliable to ±10-15 %; provider projections less so (sourced from documentation).

---

## 2. Scope — free options only

### 2.1 Local models (DGX Spark) — free by nature

| Ref | Model | Quantisation | Memory | Licence |
|---|---|---|---|---|
| L1 | Llama-3.1-8B-Instruct | FP16 | 16 GB | open |
| L2 | Llama-3.1-8B-Instruct | FP8 | 8 GB | open |
| L3 | Mistral-7B-Instruct | FP16 | 14.5 GB | Apache 2.0 |
| L4 | Qwen2.5-7B-Instruct | FP16 | 15 GB | Apache 2.0 |
| L5 | Qwen2.5-14B-Instruct | FP16 | 29 GB | Apache 2.0 |

### 2.2 Providers with a permanent free tier

| Ref | Provider | Free quota | Credit card |
|---|---|---|---|
| C1 | **Groq** | ~30 req/min, daily cap | no |
| C2 | **Cerebras** | **1 M tokens/day** | no |
| C3 | **Mistral** | 1 B tokens/month, **2 req/min** | phone verification |
| C4 | **Google AI Studio** | reduced quotas since late 2025 | no |
| C5 | **OpenRouter** | ~30 models tagged `:free` | no |
| C6 | **NVIDIA NIM** | starter credits | no |

Excluded: Together, Fireworks, DeepInfra, OpenAI, Anthropic — paid, or non-renewable
trial credits.

---

## 3. Performance

### 3.1 Local models

| Ref | Model | Theoretical | **Projected** | Basis |
|---|---|---|---|---|
| L1 | Llama-3.1-8B FP16 | 17.1 | **14.0** | **measured ✅** |
| L2 | Llama-3.1-8B FP8 | 34.1 | **28.0** | calculation |
| L3 | Mistral-7B FP16 | 18.8 | **15.4** | calculation |
| L4 | Qwen2.5-7B FP16 | 18.2 | **14.9** | calculation |
| L5 | Qwen2.5-14B FP16 | 9.4 | **7.7** | calculation |

*tokens/second, single-request generation*

At comparable size, the model family matters little — memory footprint dominates.
Moving to 14B halves throughput; FP8 quantisation doubles it.

### 3.2 Free-tier providers

| Ref | Provider | **Projected throughput** | Latency (~1,265 tok) | Basis |
|---|---|---|---|---|
| C1 | Groq | **737** | 1,720 ms | **measured ✅** |
| C2 | Cerebras | **1,400 – 2,000** | ~800 ms | vendor documentation |
| C3 | Mistral | **~90 – 120** | ~11,000 ms | **partially measured** (11.3 s) |
| C4 | Google AI Studio | ~150 – 190 | ~7,000 ms | public benchmarks |
| C5 | OpenRouter | variable | variable | depends on routing |
| C6 | NVIDIA NIM | ~100 – 200 | ~7,000 ms | projection |

### 3.3 Cloud / local ratios

On the identical-model pair (Llama-3.1-8B):

| Comparison | Ratio | Basis |
|---|---|---|
| Groq / DGX FP16 | **×53** | **measured ✅** |
| Groq / DGX FP8 | ×26 | calculation |
| Cerebras / DGX FP16 | ×100 to ×143 | projection |

**No free provider is slower than local hosting.** The gap ranges from ×7 to ×143.

---

## 4. Robustness — the decisive criterion

This is where the ranking reverses.

| Ref | Source | Success rate | Behaviour | Basis |
|---|---|---|---|---|
| L1-L5 | **DGX Spark** | **100 %** | no interruption | **measured ✅** |
| C1 | Groq free | **degraded** | 429 from 2nd-3rd call | **measured ✅** |
| C2 | Cerebras free | to be measured | 1 M tok/day ≈ 2-3 full runs | projection |
| C3 | Mistral free | degraded | **2 req/min** = very constraining | partially measured |
| C4 | Google AI Studio | to be measured | quotas cut 50-80 % in late 2025 | projection |
| C5 | OpenRouter | degraded | `:free` models withdrawn without notice | **measured ✅** (404) |

### What experience showed

The pipeline consumes **~314 LLM calls and ~400,000 tokens** per full run (24 data
types), generating 1,000 to 1,200 tokens per call.

- **Groq**: impossible to exceed 2-3 calls before blocking, even with a new account. A
  full run is out of reach.
- **Mistral**: 2 requests/minute means ~2 h 45 of pure waiting for 314 calls.
- **OpenRouter**: `:free` models disappeared mid-project (404 errors).
- **DGX Spark**: 100 % of calls processed, full 10 h 30 run without interruption.

> **Key finding: on free tiers, no online provider allowed a full run to complete.**
> Only local hosting achieves it.

### The Cerebras case

With **1 M tokens/day**, Cerebras is the only free tier sized for this pipeline: roughly
2 to 3 full runs per day. It is **the most promising untested option** — top priority
for the benchmark.

---

## 5. Quality — evaluation grid

> Not projectable. The values below are hypotheses, to be replaced by measurements.

| Criterion | Measurement |
|---|---|
| Format compliance (JSON, code) | **automatable** from logs |
| Unusable responses | **automatable** (`Invalid skeleton` after 3 attempts) |
| Accuracy, relevance | manual scoring on a sample |

| Ref | Model | Valid format first try | Confidence |
|---|---|---|---|
| L1 | Llama-3.1-8B FP16 | ~75 % | hypothesis |
| L2 | Llama-3.1-8B FP8 | ~72 % | hypothesis |
| L4 | Qwen2.5-7B | ~80 % | hypothesis |
| L5 | Qwen2.5-14B | ~85 % | hypothesis |

**Most important hypothesis to verify**: if FP8 degrades the valid-format rate, the
doubled throughput is cancelled out by additional repair calls.

---

## 6. Cost

All options being free in cash terms, cost shifts elsewhere.

| Option | Monetary cost | Real cost |
|---|---|---|
| DGX Spark | €0 | **machine occupancy: 10 h 30 per run** (measured ✅) |
| Free providers | €0 | **prompts are often used for training** |

> **Critical point**: no-credit-card free tiers are funded by your prompts. Google uses
> data to improve its models outside the EU/UK/EEA, and Mistral's Experiment tier
> requires an opt-in.
>
> For potentially re-identifiable mobility data, **this disqualifies free tiers** as
> soon as you move beyond the synthetic test set.

---

## 7. Summary

| | DGX Spark | Groq | Cerebras | Mistral | OpenRouter |
|---|---|---|---|---|---|
| Throughput | ★☆☆☆☆ | ★★★★☆ | ★★★★★ | ★★☆☆☆ | ★★★☆☆ |
| Quota | ★★★★★ | ★☆☆☆☆ | ★★★★☆ | ★★☆☆☆ | ★★☆☆☆ |
| Reliability | ★★★★★ | ★★☆☆☆ | ★★★☆☆ | ★★☆☆☆ | ★☆☆☆☆ |
| Privacy | ★★★★★ | ★☆☆☆☆ | ★☆☆☆☆ | ★☆☆☆☆ | ★☆☆☆☆ |
| Full run possible | ✅ | ❌ | ⚠️ | ❌ | ❌ |

---

## 8. Deviation log

> To be filled in after running the benchmark.

| Ref | Metric | Projected | Measured | Deviation | Explanation |
|---|---|---|---|---|---|
| L1 | throughput | 14.0 | 14.0 ✅ | 0 % | calibration |
| L2 | throughput | 28.0 | | | |
| L3 | throughput | 15.4 | | | |
| L4 | throughput | 14.9 | | | |
| L5 | throughput | 7.7 | | | |
| C1 | throughput | 737 | 737 ✅ | 0 % | calibration |
| C2 | throughput | 1,400-2,000 | | | |
| C2 | full run? | yes (proj.) | | | **priority test** |
| L1 | valid format | ~75 % | | | |
| L2 | valid format | ~72 % | | | |
| — | concurrency ×4 | ~50-60 | | | |

Any deviation above 20 % must be explained, not merely noted.

---

## 9. Conclusion and recommendation

### The best option is the one already in place

> **Recommendation: keep the current hybrid architecture**
> `LLM_ROUTER_ORDER=Groq,NVIDIA` — cloud first, automatic fallback to local.

No single option works on its own, and that is precisely what justifies the hybrid:

**Cloud alone is disqualified** by quotas. Groq, Mistral and OpenRouter all failed to
complete a full run — measured, not assumed. Free cloud tiers are structurally sized for
prototyping, not for 314 consecutive calls.

**Local alone is viable but slow**: 100 % reliability, but 10 h 30 per full run. Usable,
not optimal.

**The hybrid takes the best of both.** On the measured run, the split was almost even —
**127 Groq calls versus 125 local calls** on stage S3. The cloud accelerates while quota
lasts, local guarantees completion. It is the only tested configuration that finishes.

### One improvement to test: add Cerebras

Cerebras is the only free tier genuinely sized for this pipeline (1 M tokens/day ≈ 2-3
full runs), and it advertises the highest throughput on the market.

Configuration to evaluate:

```dotenv
LLM_ROUTER_ORDER=Cerebras,Groq,NVIDIA
```

Cerebras would absorb the bulk of the volume, Groq would take over, local would
guarantee completion. **This is the benchmark's priority test** — it could substantially
reduce the 10 h 30.

### The criterion that overrides all others

No-credit-card free tiers are funded by prompts. On real mobility data, potentially
re-identifiable, **this disqualifies any cloud option**, regardless of the performance
gap.

Hence the usage rule:

| Data type | Configuration |
|---|---|
| Synthetic test set | hybrid `Cerebras,Groq,NVIDIA` — maximum speed |
| **Real user data** | **`NVIDIA` only — local exclusively** |

This switch must be **explicit in the application**, not left to configuration chance.

### Order of actions

| # | Action | Effort | Expected gain |
|---|---|---|---|
| 1 | Test Cerebras and place it first in the chain | ½ d | large reduction in run time |
| 2 | Extract quality rates from logs | ½ d | fills the missing protocol axis |
| 3 | Replay one case 5× (variability) | ½ d | unmet acceptance criterion |
| 4 | Test FP8 (throughput ×2) and its quality impact | ½ d | ×2 if quality holds |
| 5 | Force local-only mode on real data | ½ d | compliance |

---

## 10. Measurement conditions

| Item | Value (existing measurements) |
|---|---|
| Hardware | DGX Spark GB10, 128 GB unified, 273 GB/s, single GPU |
| Stack | vLLM 0.15.1, tp=1, gpu-mem-util=0.25, max-model-len=8192 |
| Local model | NousResearch/Meta-Llama-3.1-8B-Instruct |
| Cloud model | llama-3.1-8b-instant (Groq), July 2026 |
| Machine load | ~50 GB occupied by third-party processes |
| Provider quotas | verified May 2026 |

> Free-tier quotas **change frequently** and online models evolve without notice. Any
> undated measurement becomes uninterpretable.
