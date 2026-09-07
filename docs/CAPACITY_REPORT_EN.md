# Capacity Report — Mobimix on DGX Spark

**Question**: how many people can use the application simultaneously, and what is
needed to support around ten users?

**Short answer**: today, **1 analysis at a time**. With one week of optimisation,
**6 to 8 in parallel** — comfortably covering a dozen users. **No additional hardware
is required.**

---

## 1. What limits capacity

The bottleneck is neither the CPU, the network, nor the software: it is **memory
bandwidth**.

To produce a single token, the model must re-read all of its active weights from
memory. The model in use (Llama-3.1-8B) is ~16 GB, and the machine reads at 273 GB/s.
Hence the ceiling:

```
273 GB/s ÷ 16 GB = ~17 tokens/s in theory
                    14 tokens/s measured (82 % of ceiling — normal)
```

This limit is **structural**. No software setting works around it: the GPU spends its
time waiting on memory, not computing.

### Hardware context

| Item | Value | Consequence |
|---|---|---|
| Memory bandwidth | **273 GB/s** | caps generation speed |
| Unified memory | 128 GB (CPU + GPU shared) | ~50 GB already used by third-party services |
| GPU | 1 only (GB10) | no multi-GPU distribution possible |
| Machine | **shared** with other users | resources not guaranteed |

---

## 2. Current capacity

| Indicator | Value |
|---|---|
| Concurrent analyses | **1** |
| Duration per data type | 25 to 40 min |
| Analyses per hour | ~2 |
| Analyses per day (8 h) | ~15 |

The application deliberately enforces a lock: one analysis at a time. This is not an
absolute technical limit but a safeguard — two concurrent jobs on the current
configuration would slow each other down with no net gain.

### What this means with several users

Scenario: 5 people each upload a file with 5 data types (25 types total).

- Sequential processing: **~15 hours**
- The 5th person waits all day
- Worse: there is **no queue** — they must come back and click again themselves

This is the main friction point for collective use.

---

## 3. The three optimisation levers

### Lever 1 — FP8 quantisation ⭐ best effort/gain ratio

The model goes from 16 GB to ~8 GB. Since speed depends on
`bandwidth ÷ model size`, halving the size **doubles throughput**.

- Throughput: 14 → **~26 tokens/s**
- Duration per type: ~15 min → **~8 min** (pure LLM time)
- Effort: **½ day** — configuration change, no code
- Risk: minimal quality loss on a model this size

### Lever 2 — Batching

The pipeline currently calls the LLM **sequentially**: one request, wait, next. Yet
vLLM can process several requests in the **same memory pass**. Since the dominant cost
is re-reading the weights, serving 6 requests in parallel costs barely more than one.

- Throughput **per request**: unchanged (~26 tok/s)
- **Total machine** throughput: ~26 → **~130-180 tokens/s aggregated**
- Concurrent users: 1 → **6 to 8**
- Effort: **2 to 4 days** — rewriting the orchestration loop

This is the lever that multiplies the number of users.

### Lever 3 — Job queue

Does not make the system faster, but **transforms the experience**. The user uploads
their file, it is queued immediately, and the interface shows "3rd in line, estimated
start in 40 min". No need to come back and click.

- Effort: **~1 day** for a simple version
- Gain: perceived, not measurable — but it is what is most missing today

### Complementary lever — freeing memory

Two LLM servers owned by system administration permanently occupy ~50 GB. Since the
quantised model only needs 8 GB, reclaiming part of that would enlarge the attention
cache, which directly determines how many requests fit in parallel.

- Effort: a conversation with the machine administrator
- Gain: determines the upper end of the range (8 rather than 4 users)

---

## 4. Capacity after optimisation

| Indicator | Today | Optimised | Factor |
|---|---|---|---|
| Throughput per request | 14 tok/s | ~26 tok/s | ×1.9 |
| Total machine throughput | 14 tok/s | ~130-180 tok/s | **×10** |
| Concurrent analyses | 1 | **6 to 8** | ×7 |
| Duration per analysis | 25-40 min | **12-18 min** | ÷2 |
| Analyses per hour | ~2 | ~25 | ×12 |
| Analyses per day (8 h) | ~15 | **~200** | ×13 |

**On the 5 people × 5 types scenario**: ~15 hours → **~2 hours**.

---

## 5. Is this enough for a dozen users?

**Yes, comfortably.** Two often-confused notions must be distinguished.

### Connected users vs concurrent analyses

**Viewing and downloading reports** uses neither the GPU nor the LLM. It is file
transfer: **20 to 50 people** can do it simultaneously without difficulty, today.

**Launching an analysis** is the only expensive operation. That is where the limit sits.

### The maths for 10 people

A dozen users does not mean ten simultaneous analyses. In real usage, volume is far
lower:

| Usage assumption | Requests/day | Optimised capacity | Verdict |
|---|---|---|---|
| One analysis per person per week | ~2 | ~200 | ✅ very comfortable |
| One analysis per person per day | ~10 | ~200 | ✅ comfortable |
| Three analyses per person per day | ~30 | ~200 | ✅ still holds |
| Peak: everyone at once | 10 concurrent | 6-8 in parallel | ⚠️ ~20 min queue |

Even in the worst case — all ten launching within the same minute — the queue absorbs
the peak with about twenty minutes of waiting. Acceptable for a batch processing tool.

---

## 6. Is more hardware needed?

**No.** The current DGX Spark is sufficient for a dozen users, provided the
optimisations are applied.

### When a second machine would become justified

| Situation | Signal | Response |
|---|---|---|
| ~50 active users | queue regularly > 2 h | second machine |
| Real-time requirement | 15 min wait unacceptable | cloud API for peaks |
| Much larger model | 8B quality insufficient | hardware with more bandwidth |
| Critical availability | outage = service down | second machine for redundancy |

### Cheaper alternatives to buying hardware

**Hybrid mode** — already configured (`LLM_ROUTER_ORDER=Groq,NVIDIA`). The cloud
absorbs the start of processing while quota allows, then the local machine takes over.
A paid cloud subscription would smooth peaks without buying hardware.

**Reduce scope per request** — limiting files to 2 rows instead of 5 divides
monopolisation time by 2.5. One-line configuration change, immediate effect on
fluidity.

**Smaller model** — a 3-billion-parameter model would run ~2.5× faster, at the cost of
report quality. Only worth considering if volume explodes.

---

## 7. Opening the application to a dozen people

### Access (already operational)

The application exposes **no port to the internet**. Access goes through an SSH tunnel:
each person runs one command from their workstation and opens `http://localhost:8501`.

This assumes they have an **SSH account on the machine**. For a circle of colleagues
this is the simplest and safest option — nothing extra to install, and de facto
two-factor authentication (SSH key + application password).

For people without an SSH account, a Cloudflare-type tunnel would be needed — plus
**approval from the administrator**, since the machine is shared.

### Already in place for collective use

- password authentication, mandatory
- identification of who submitted each request (traceability)
- file validation before processing (columns, size, row count)
- caps: 5 MB and 5 rows maximum per file
- lock preventing GPU saturation

### What remains to be done

1. **Job queue** — the most visible gap in collective use
2. **FP8 quantisation** — halves waiting times
3. **Batching** — moves to 6-8 concurrent analyses
4. *(optional)* per-user report partitioning, if the data warrants it

---

## 8. Recommended plan

| Order | Action | Effort | Gain |
|---|---|---|---|
| 1 | FP8 quantisation | ½ d | throughput ×2, no risk |
| 2 | Job queue | 1 d | user experience |
| 3 | Batching | 2-4 d | 6-8 concurrent users |
| 4 | Negotiate memory | discussion | consolidates item 3 |

**Total: about one week** to go from 1 to 6-8 concurrent analyses and halve durations —
with no hardware investment.

Rationale for the order: FP8 requires no code change and gives an immediate measurable
gain; the queue solves the most-felt problem at low cost; batching, heavier and riskier,
comes last.

---

## 9. Reliability of these figures

For methodological honesty:

| Data point | Status |
|---|---|
| 14 tokens/s today | **measured** (244 tokens in 17.4 s) |
| 25-40 min per type | **measured** (full run: 10 h 30 for 24 types) |
| ~26 tok/s with FP8 | **solid projection** (follows from size ratio) |
| 6-8 concurrent analyses | **estimate** — depends on available attention cache |
| ~200 analyses/day | **estimate** derived from the above |

The parallelism estimates need validation through testing. It is possible to plateau at
4 rather than 8 if available memory remains limited by third-party services.

---

## Conclusion

The application can serve **a dozen users with no additional hardware**.

The real limit is not the number of connected users — several dozen can view reports
right now — but **processing throughput**, capped by the machine's memory bandwidth.

One week of optimisation moves capacity from 1 to 6-8 concurrent analyses and halves
waiting times, comfortably covering the intended usage. A second machine would only be
justified beyond ~50 active users, or if a real-time response requirement emerged.
