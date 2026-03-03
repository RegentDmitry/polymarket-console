"""CLI for Skilled Trader Scouting System.

Usage:
    python -m crypto.scouting.cli init              # create DB schema
    python -m crypto.scouting.cli scan [--events N]  # scan resolved events
    python -m crypto.scouting.cli scan-event SLUG    # scan specific event
    python -m crypto.scouting.cli enrich [--limit N]  # load trader histories
    python -m crypto.scouting.cli score              # compute skill scores
    python -m crypto.scouting.cli mm                 # detect market makers
    python -m crypto.scouting.cli top [--limit N]    # show top traders
    python -m crypto.scouting.cli trader ADDRESS     # inspect a trader
    python -m crypto.scouting.cli stats              # DB statistics
    python -m crypto.scouting.cli pipeline [--events N]  # full pipeline: scan → enrich → mm → score
"""

import argparse
import sys

from . import db
from .scanner import scan_resolved_events, scan_event, enrich_traders
from .scorer import score_all_traders
from .filters import run_mm_detection


def cmd_init(args):
    db.init_schema()
    print("Schema initialized OK")


def cmd_scan(args):
    scan_resolved_events(max_events=args.events, category=args.category,
                         min_volume=args.min_volume, verbose=True)


def cmd_scan_event(args):
    scan_event(args.slug, load_holders=True, verbose=True)
    stats = db.get_db_stats()
    print(f"DB: {stats['traders']} traders, {stats['markets']} markets")


def cmd_enrich(args):
    enrich_traders(limit=args.limit, verbose=True)


def cmd_score(args):
    score_all_traders(verbose=True)


def cmd_mm(args):
    run_mm_detection(verbose=True)


def cmd_top(args):
    top = db.get_top_traders(limit=args.limit, exclude_mm=not args.include_mm)
    if not top:
        print("No scored traders found. Run 'score' first.")
        return

    print(f"Top {len(top)} skilled traders:")
    print(f"  {'#':<4} {'Alias':<25} {'Score':>7} {'Markets':>8} {'W/L':>8} {'PnL':>10} {'ROI':>7} {'MM':>4}")
    print(f"  {'-'*80}")
    for i, t in enumerate(top):
        name = t["alias"] or t["address"][:16]
        wl = f"{t['win_count']}/{t['loss_count']}"
        mm = "MM" if t["is_mm"] else ""
        print(f"  {i+1:<4} {name[:25]:<25} {t['skill_score']:>7.2f} {t['total_markets']:>8} "
              f"{wl:>8} ${t['realized_pnl']:>+9,.0f} {t['avg_roi']:>+6.1f}% {mm:>4}")


def cmd_trader(args):
    addr = args.address
    trader = db.get_trader(addr)
    if not trader:
        # Try to find by alias prefix
        with db.get_cursor(commit=False) as cur:
            cur.execute("SELECT * FROM traders WHERE alias ILIKE %s LIMIT 1", (f"%{addr}%",))
            trader = cur.fetchone()
        if not trader:
            print(f"Trader not found: {addr}")
            return

    print(f"\n{'='*60}")
    print(f"  {trader['alias'] or 'Unknown'}")
    print(f"  Address: {trader['address']}")
    print(f"{'='*60}")
    print(f"  Skill Score: {trader['skill_score']:.2f}")
    print(f"  Markets: {trader['total_markets']}")
    print(f"  Win/Loss: {trader['win_count']}/{trader['loss_count']} "
          f"({trader['win_count']/(trader['win_count']+trader['loss_count'])*100:.0f}% WR)" if trader['win_count'] + trader['loss_count'] > 0 else "")
    print(f"  Realized PnL: ${trader['realized_pnl']:+,.2f}")
    print(f"  ROI: {trader['avg_roi']:+.1f}%")
    print(f"  Invested: ${trader['total_invested']:,.2f}")
    print(f"  MM: {'YES' if trader['is_mm'] else 'NO'}")

    # Show positions
    positions = db.get_trader_positions(trader["address"])
    if positions:
        open_pos = [p for p in positions if not p["is_closed"]]
        closed_pos = [p for p in positions if p["is_closed"]]

        if open_pos:
            print(f"\n  Open positions ({len(open_pos)}):")
            for p in open_pos[:20]:
                title = p.get("title", "?")[:40]
                print(f"    {p['outcome_side']:>3} {title:<40} "
                      f"size={p['size']:,.0f} entry={p['avg_price']:.2f}")

        if closed_pos:
            print(f"\n  Closed positions ({len(closed_pos)}):")
            for p in closed_pos[:20]:
                title = p.get("title", "?")[:40]
                pnl = p["realized_pnl"]
                print(f"    {p['outcome_side']:>3} {title:<40} "
                      f"PnL=${pnl:+,.2f} ({p['percent_pnl']:+.0f}%)")


def cmd_stats(args):
    stats = db.get_db_stats()
    print(f"Database statistics:")
    print(f"  Traders:          {stats['traders']:,}")
    print(f"  Markets:          {stats['markets']:,}")
    print(f"  Resolved markets: {stats['resolved_markets']:,}")
    print(f"  Positions:        {stats['positions']:,}")

    with db.get_cursor(commit=False) as cur:
        cur.execute("SELECT COUNT(*) as n FROM traders WHERE is_mm = TRUE")
        mm = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) as n FROM traders WHERE skill_score > 0")
        scored = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) as n FROM traders WHERE total_markets >= 10")
        active = cur.fetchone()["n"]

    print(f"  Market makers:    {mm:,}")
    print(f"  Scored traders:   {scored:,}")
    print(f"  Active (10+ mkts):{active:,}")


def cmd_pipeline(args):
    """Full pipeline: scan → enrich → detect MM → score."""
    print("=== PHASE 1: Scan resolved events ===")
    scan_resolved_events(max_events=args.events, category=args.category,
                         min_volume=args.min_volume, verbose=True)

    print("\n=== PHASE 2: Enrich trader histories ===")
    enrich_traders(verbose=True)

    print("\n=== PHASE 3: Detect market makers ===")
    run_mm_detection(verbose=True)

    print("\n=== PHASE 4: Compute skill scores ===")
    score_all_traders(verbose=True)

    print("\n=== DONE ===")
    cmd_stats(args)


def main():
    parser = argparse.ArgumentParser(description="Skilled Trader Scouting System")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="Initialize DB schema")

    p_scan = sub.add_parser("scan", help="Scan resolved events")
    p_scan.add_argument("--events", type=int, default=50, help="Max events to scan")
    p_scan.add_argument("--category", type=str, default="politics", help="Category filter (politics/crypto/sports/other, default: politics)")
    p_scan.add_argument("--min-volume", type=float, default=10000, help="Min event volume (default: $10k)")

    p_se = sub.add_parser("scan-event", help="Scan a specific event")
    p_se.add_argument("slug", help="Event slug")

    p_enrich = sub.add_parser("enrich", help="Load full trader histories")
    p_enrich.add_argument("--limit", type=int, default=None, help="Limit traders to enrich")

    sub.add_parser("score", help="Compute skill scores")
    sub.add_parser("mm", help="Detect market makers")

    p_top = sub.add_parser("top", help="Show top traders")
    p_top.add_argument("--limit", type=int, default=30, help="Number of traders")
    p_top.add_argument("--include-mm", action="store_true", help="Include market makers")

    p_trader = sub.add_parser("trader", help="Inspect a trader")
    p_trader.add_argument("address", help="Wallet address or alias")

    sub.add_parser("stats", help="DB statistics")

    p_pipe = sub.add_parser("pipeline", help="Full pipeline: scan → enrich → mm → score")
    p_pipe.add_argument("--events", type=int, default=50, help="Max events to scan")
    p_pipe.add_argument("--category", type=str, default="politics", help="Category filter")
    p_pipe.add_argument("--min-volume", type=float, default=10000, help="Min event volume")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    commands = {
        "init": cmd_init,
        "scan": cmd_scan,
        "scan-event": cmd_scan_event,
        "enrich": cmd_enrich,
        "score": cmd_score,
        "mm": cmd_mm,
        "top": cmd_top,
        "trader": cmd_trader,
        "stats": cmd_stats,
        "pipeline": cmd_pipeline,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
