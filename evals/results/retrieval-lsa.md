# Runbook retrieval: BM25 vs dense vs hybrid

Embedder: `lsa` · 10 runbooks, 30 step chunks. Scored per runbook. Counts are the number of queries whose right runbook was ranked first (R@1) or in the top 3 (R@3).

## written, filter on (n=50)

| Method | R@1 | R@3 | MRR |
|---|---|---|---|
| bm25 | 42/50 (84%) | 47/50 (94%) | 0.892 |
| dense | 46/50 (92%) | 49/50 (98%) | 0.947 |
| hybrid | 46/50 (92%) | 49/50 (98%) | 0.954 |

## written, filter off (n=50)

| Method | R@1 | R@3 | MRR |
|---|---|---|---|
| bm25 | 39/50 (78%) | 44/50 (88%) | 0.846 |
| dense | 40/50 (80%) | 46/50 (92%) | 0.868 |
| hybrid | 42/50 (84%) | 47/50 (94%) | 0.892 |

## written, everyday words only, filter off (n=30)

| Method | R@1 | R@3 | MRR |
|---|---|---|---|
| bm25 | 19/30 (63%) | 24/30 (80%) | 0.743 |
| dense | 20/30 (67%) | 26/30 (87%) | 0.78 |
| hybrid | 22/30 (73%) | 27/30 (90%) | 0.821 |

## written, shared terms only, filter off (n=20)

| Method | R@1 | R@3 | MRR |
|---|---|---|---|
| bm25 | 20/20 (100%) | 20/20 (100%) | 1.0 |
| dense | 20/20 (100%) | 20/20 (100%) | 1.0 |
| hybrid | 20/20 (100%) | 20/20 (100%) | 1.0 |

## incidents, filter on (n=30)

| Method | R@1 | R@3 | MRR |
|---|---|---|---|
| bm25 | 25/30 (83%) | 28/30 (93%) | 0.891 |
| dense | 25/30 (83%) | 29/30 (97%) | 0.908 |
| hybrid | 24/30 (80%) | 29/30 (97%) | 0.877 |

