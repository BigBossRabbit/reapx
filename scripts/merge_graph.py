import json, shutil

BASE = '/Users/fromthejump/reapx/data/'
shutil.copy(BASE + 'x_graph_clean.json', BASE + 'x_graph_clean.json.bak_1256')

a = json.load(open(BASE + 'x_graph_v6.json'))            # 2346 followers (new, best)
b = json.load(open(BASE + 'x_graph_clean.json.bak_1256'))  # 1256 followers (earlier clean)


def merge(key):
    """Union on handle. Prefer whichever record has more populated fields."""
    out = {}
    for src in (b, a):  # a last so newer wins ties
        for r in src.get(key, []):
            h = r.get('handle')
            if not h:
                continue
            prev = out.get(h)
            if prev is None:
                out[h] = r
                continue
            rank = lambda x: (bool(x.get('bio')), bool(x.get('name')))
            if rank(r) >= rank(prev):
                out[h] = r
    return list(out.values())


fo = merge('followers')
fl = merge('following')
hf = {x['handle'] for x in fo}
hl = {x['handle'] for x in fl}

a['followers'] = fo
a['following'] = fl
a['stats'] = {
    'followers_count': len(fo),
    'following_count': len(fl),
    'mutual_count': len(hf & hl),
}
meta = a.setdefault('meta', {})
meta['merge_note'] = (
    'Union of x_graph_v6.json (2346 followers, captured after X throttle cleared '
    'with the Followers-tab fix) and the earlier clean 1256-follower capture. '
    'Deduped on handle; richer record wins.'
)

json.dump(a, open(BASE + 'x_graph_clean.json', 'w'), indent=2, ensure_ascii=False)

print('MERGED ->', a['stats'])
print('total unique connections:', len(hf | hl))
print('gained vs v6 alone      :', len(fo) - 2346, 'followers')
