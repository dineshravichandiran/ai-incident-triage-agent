### llm-eval

Backend: `command` · set: eval (20 incidents) · repeats: 2 · 2026-11-13 · code ed6259b

| Context | Cause correct | Right runbook | Quoted lines not in context | Mean input tokens (reported) | Same, minus fixed overhead | Mean latency |
|---|---|---|---|---|---|---|
| raw | 40 / 40 | 40 / 40 | 0 of 120 | 29,886 | 28,119 | 6.55s |
| filtered | 40 / 40 | 40 / 40 | 0 of 116 | 2,618 | 851 | 5.18s |

Filtered input is 91% fewer tokens than raw as reported, 97% fewer once the fixed overhead (1,767 tokens per call) is taken out.
