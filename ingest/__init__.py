"""Phase 3 ingestion: pull rumour posts, extract structured claims, dedup, cluster.

Pipeline (each stage injectable for offline tests):

    RSS feeds ──> sources.fetch_all ──> raw posts
                                          │  dedup on post URL (store.add_post)
                                          v
                       engine.run.analyze (LLM extract)  ──> structured claim
                                          │  drop non-transfer / low-confidence
                                          v
                    cluster.deal_key(player, window)  ──> claim attached to a deal
                                          │
                                          v
                              SQLite (ingest.db): posts + claims

Ingested claims are PROPOSED — they live in SQLite, separate from the curated
journalist_claims.csv, and do not affect the leaderboard until promoted (same
verified-gate posture as auto-resolved outcomes, see ground_truth.py).
"""
