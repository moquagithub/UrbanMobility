#!/usr/bin/env python3
"""
Compare les performances d'inférence LLM enregistrées dans `llm_calls_log`.

Chaque run (cloud vs DGX) utilise un provider différent, donc GROUP BY provider
sépare naturellement les deux. Sortie : latence (moy/médiane/p95), débit
tokens/s, taux de succès et durée totale de passe, par provider.

Usage :
    # Sur l'hôte (port 3306 exposé par docker-compose) :
    pip install mysql-connector-python
    python benchmark_compare.py

    # Ou dans le réseau docker :
    docker compose run --rm --entrypoint python pipeline benchmark_compare.py

Options :
    --step s3_algorithms   ne garder qu'une étape
    --since "2026-07-22 16:00:00"   ne garder que les appels après cette date
"""
import argparse
import os
import statistics as st
from datetime import datetime

import mysql.connector


def pct(values, p):
    """Percentile p (0-100) sur une liste, sans dépendance externe."""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", default=None, help="filtrer sur une étape (ex. s3_algorithms)")
    ap.add_argument("--since", default=None, help="date mini 'YYYY-MM-DD HH:MM:SS'")
    args = ap.parse_args()

    conn = mysql.connector.connect(
        host=os.environ.get("DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("DB_PORT", "3306")),
        database=os.environ.get("DB_NAME", "urbain_automation"),
        user=os.environ.get("DB_USER", "root"),
        password=os.environ.get("DB_PASSWORD", ""),
    )
    cur = conn.cursor(dictionary=True)

    where, params = [], []
    if args.step:
        where.append("step = %s")
        params.append(args.step)
    if args.since:
        where.append("called_at >= %s")
        params.append(args.since)
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    cur.execute(
        f"""SELECT provider, model, tokens_in, tokens_out, duration_ms,
                   success, called_at
            FROM llm_calls_log {clause}
            ORDER BY provider, called_at""",
        params,
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        print("Aucun appel trouvé dans llm_calls_log (avec ces filtres).")
        return

    # Regroupe par provider
    by_prov = {}
    for r in rows:
        by_prov.setdefault(r["provider"], []).append(r)

    print(f"\n{'PROVIDER':<14}{'MODEL':<26}{'N':>5}{'OK%':>6}"
          f"{'moy ms':>9}{'p50 ms':>9}{'p95 ms':>9}{'tok/s':>8}{'passe':>9}")
    print("-" * 103)

    for prov, rs in sorted(by_prov.items()):
        ok = [r for r in rs if r["success"]]
        durs = [r["duration_ms"] for r in ok]
        tok_out = sum(r["tokens_out"] for r in ok)
        model = (ok[0]["model"] if ok else rs[0]["model"])[:25]

        # Débit agrégé : tokens produits / temps d'inférence cumulé
        total_s = sum(durs) / 1000.0
        tok_per_s = (tok_out / total_s) if total_s else 0.0

        # Durée de passe : du 1er au dernier appel (temps mur réel)
        spans = [r["called_at"] for r in rs]
        wall = (max(spans) - min(spans)).total_seconds()
        wall_str = f"{wall/60:.1f}m" if wall >= 60 else f"{wall:.0f}s"

        ok_pct = 100.0 * len(ok) / len(rs)
        print(f"{prov:<14}{model:<26}{len(rs):>5}{ok_pct:>5.0f}%"
              f"{(st.mean(durs) if durs else 0):>9.0f}"
              f"{pct(durs, 50):>9.0f}{pct(durs, 95):>9.0f}"
              f"{tok_per_s:>8.1f}{wall_str:>9}")

    print("-" * 103)
    print("Lecture : 'moy/p50/p95 ms' = latence par appel (plus bas = mieux) ;")
    print("          'tok/s' = débit de génération (plus haut = mieux) ;")
    print("          'passe' = temps mur entre le 1er et le dernier appel du provider.\n")


if __name__ == "__main__":
    main()
