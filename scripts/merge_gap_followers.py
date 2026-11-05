#!/usr/bin/env python3
"""merge_gap_followers.py — merge the new Brave-session follower harvest into the
master scored dataset. Grow-only: only ADDS handles not already present; never
deletes/overwrites existing enrichment."""
import json
from pathlib import Path

# Repo data dir (script lives in <repo>/scripts/).
DATA = Path(__file__).resolve().parent.parent / 'data'
SCORED = str(DATA / 'x_crm_scored.json')
GAP = str(DATA / 'x_followers_gap_fill.json')
OUT = str(DATA / 'x_crm_scored.json')  # master (read-modify-write, grow-only)

def main():
    scored = json.load(open(SCORED))
    nodes = scored['nodes']
    by_handle = {n['handle']: n for n in nodes}

    gap = json.load(open(GAP))
    gap_followers = gap.get('followers', [])
    gap_following = gap.get('following', [])

    # --- followers merge ---
    new_followers = []
    for f in gap_followers:
        h = f.get('handle') if isinstance(f, dict) else f
        if not h:
            continue
        if h in by_handle:
            # already present — just ensure is_follower is true
            if not by_handle[h].get('is_follower'):
                by_handle[h]['is_follower'] = True
        else:
            new_followers.append({
                'handle': h,
                'name': f.get('name') if isinstance(f, dict) else '',
                'bio': f.get('bio') if isinstance(f, dict) else '',
                'verified': bool(f.get('verified')) if isinstance(f, dict) else False,
                'follows_you': bool(f.get('follows_you')) if isinstance(f, dict) else True,
                'segments': [],
                'primary_segment': 'UNKNOWN',
                'influence_score': 0.0,
                'pagerank': 0.0,
                'tier': 'T4_POTENTIAL',  # default; re-score later
                'bio_signal': 0.0,
                'geo_score': 0.0,
                'mutual': False,
                'is_follower': True,
                'is_following': False,
                'shitcoiner': False,
            })

    # --- following merge (new following-only accounts) ---
    new_following = []
    for f in gap_following:
        h = f.get('handle') if isinstance(f, dict) else f
        if not h:
            continue
        if h in by_handle:
            if not by_handle[h].get('is_following'):
                by_handle[h]['is_following'] = True
            # mutual? both flags true
            if by_handle[h].get('is_follower'):
                by_handle[h]['mutual'] = True
        else:
            new_following.append({
                'handle': h,
                'name': f.get('name') if isinstance(f, dict) else '',
                'bio': f.get('bio') if isinstance(f, dict) else '',
                'verified': bool(f.get('verified')) if isinstance(f, dict) else False,
                'follows_you': False,
                'segments': [],
                'primary_segment': 'UNKNOWN',
                'influence_score': 0.0,
                'pagerank': 0.0,
                'tier': 'T4_POTENTIAL',
                'bio_signal': 0.0,
                'geo_score': 0.0,
                'mutual': False,
                'is_follower': False,
                'is_following': True,
                'shitcoiner': False,
            })

    before = len(nodes)
    nodes.extend(new_followers)
    nodes.extend(new_following)
    after = len(nodes)

    scored['nodes'] = nodes
    scored['total_nodes'] = after
    scored['generated_at'] = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
    scored['merge_note'] = (f"Gap-fill merge: +{len(new_followers)} followers, "
                            f"+{len(new_following)} following from Brave-session harvest "
                            f"({len(gap_followers)} followers / {len(gap_following)} following in harvest file).")

    json.dump(scored, open(OUT, 'w'), indent=1)
    print(f"before: {before}  after: {after}  (+{after-before})")
    print(f"  +{len(new_followers)} new followers, +{len(new_following)} new following")
    print(f"  total is_follower: {sum(1 for n in nodes if n.get('is_follower'))}")
    print(f"  total is_following: {sum(1 for n in nodes if n.get('is_following'))}")
    print(f"  total mutual: {sum(1 for n in nodes if n.get('is_follower') and n.get('is_following'))}")
    print(f"merge_note: {scored['merge_note']}")

if __name__ == '__main__':
    main()