# CRM METHODOLOGY — ReapX Graph CRM

**For:** @OKIN_17 (Nikolai Tjongarero)
**Status:** Specification for Doctor Doom
**Data source:** `data/x_graph_v3.json` / `data/x_crm_graph.json`
**Generated:** 2026-08-18

---

## 0. GROUND TRUTH — what we actually have, what we do not

### Available fields per node
| Field | Type | Source | Notes |
|-------|------|--------|-------|
| `handle` | string | DOM parse | stable identifier |
| `name` | string | DOM parse | display name, may be empty |
| `bio` | string | DOM parse | **the richest signal we have**; may be empty |
| `verified` | bool | DOM parse | blue/gold checkmark |
| `avatar_url` | string | DOM parse | profile image |
| `follows_you` | bool | DOM parse (`Follows you` token) | only reliable on the followers list |
| `is_follower` | bool | derived | appears in followers list |
| `is_following` | bool | derived | appears in following list |
| `is_mutual` | bool | derived | `is_follower AND is_following` |

### Graph edges available
- `OKIN_17 -> U` : Nikolai follows U
- `U -> OKIN_17` : U follows Nikolai
- mutual = both edges present

### NOT available (do not build rules on these)
- `followers_count` / `following_count` per user — null in data, not in list-view DOM
- `location` field — null in data; inferrable from bio only
- tweet content, likes, replies, retweets
- follower-of-follower (2-hop) edges
- account age, activity frequency, bot scores
- direct messages, email, phone

### Scale
- ~1,600 followers extracted (X scroll cap out of 4,706 total)
- ~1,547 following extracted
- ~282 mutuals

### Compute budget
NetworkX in-process, 8GB M1. PageRank, betweenness, Louvain, shortest path are all trivial at ~2,600 nodes.

### Topology reality check
With 1-hop-only data the graph is a **directed star** centered on OKIN_17 plus bidirectional edges for mutuals. There are **no edges among leaf nodes** yet. This means:
- Degree, in-degree, out-degree, and PageRank are computable and meaningful.
- Betweenness centrality on leaf nodes is **zero** (no leaf-to-leaf paths exist).
- Community detection on leaf nodes finds **nothing** (no leaf-leaf edges to cluster).
- Betweenness and communities only become meaningful **after 2-hop enrichment**.

The scoring and segmentation below lean on **bio signals and asymmetry patterns** because those are the only rich signals available at 1-hop. Graph-structural components are included where they work (degree, PageRank) and forward-looking where they need 2-hop data (betweenness, communities).

---

## 1. SEGMENTATION MODEL

Each connection is tagged with one or more segments. Segments are **multi-label** — a user can be both a Bitcoiner and a Developer. Detection is by keyword match against `bio` (lowercased) and `handle` (lowercased). Empty bios get `UNKNOWN`.

### 1.1 Segment definitions

| Segment | Detection rule (bio/handle, case-insensitive) | What it is FOR |
|---------|-----------------------------------------------|----------------|
| **BITCOINER** | `bitcoin`, `btc`, `₿`, `satoshi`, `sats`, `s2f`, `node runner`, `self-custody`, `nostr`, `lightning`, `hardware wallet`, `bitcoin maxi`, `bitcoin minimalist`, `bip`, `taproot`, `ordinals`, `inscriptions`, OR handle contains `btc`/`bitcoin`/`sat` | Core market for HISHE sales, consulting, speaking. Bitcoin circular-economy collaborators. The sovereign-tech ideal user. |
| **AFRICAN_TECH** | `kenya`, `kibera`, `africa`, `namibia`, `south african`, `african`, `windhoek`, `nairobi`, `johannesburg`, `cape town`, `durban`, `lagos`, `nigeria`, `ghana`, `tanzania`, `uganda`, `rwanda`, `ethiopia`, `senegal`, `cameroon`, `kinshasa` | GEO-PRIORITY monetisation (Namibia > Kenya > SA). Local HISBE pilots. Regulatory navigation partners. In-person event hosts. |
| **DEVELOPER** | `engineer`, `developer`, `dev `, `python`, `rust`, `golang`, `javascript`, `full stack`, `backend`, `frontend`, `firmware`, `protocol`, `building `, `cto`, `programmer`, `open source`, `oss`, `github`, `gitlab` | Hiring pool. HISHE contributors. B2B dev-services clients. Open-source co-maintainers. |
| **PRIVACY_SOVEREIGNTY** | `privacy`, `sovereignty`, `freedom tech`, `self-custody`, `cypherpunk`, `encryption`, `tor`, `tails`, `vpn`, `opsec`, `surveillance`, `digital rights`, `human rights`, `activist`, `journalist` (in privacy context) | HISHE ideal users. Advocacy coalition. Grant-funded digital-rights projects. |
| **ENTREPRENEUR** | `entrepreneur`, `founder`, `startup`, `ceo`, `co-founder`, `founder`, `building `, `scaling`, `saas`, `b2b`, `venture`, `angel`, `investor` | B2B clients. Potential HISHE enterprise buyers. Co-founders for ventures. Pitch targets. |
| **NGO_DEV_SECTOR** | `civil society`, `community`, `outreach`, `non-profit`, `ngo`, `helping`, `empower`, `empowering`, `orphan`, `street kids`, `refugee`, `human rights`, `charity`, `foundation`, `grant`, `donor` | Grant-funded projects. Institutional buyers (UN, AU, NGOs). Impact-story sources. Community-project participants. |
| **MEDIA_PR** | `pr `, `public relations`, `author`, `writer`, `speaker`, `journalist`, `reporter`, `media`, `consultant`, `podcast`, `conference`, `meetup organizer`, `keynote` | Amplification channel. Press tours. Podcast circuit. Narrative shaping. Do NOT sell to — serve and they broadcast. |
| **VENDOR_ORG** | `trezor`, `sparrow`, `liana`, `seedsigner`, `ledger`, `coldcard`, `foundation`, `blockstream`, `unchained`, `caravan`, `conference`, `merchandise`, `brand`, `company`, `protocol`, `foundation devices` | B2B relationships. Hardware wallet integrations. Conference sponsors. Merch partners. Referral fee sources. |
| **SHITCOINER** (penalty flag) | `ethereum`, `eth `, `solana`, `altcoin`, `shitcoin`, `memecoin`, `airdrop`, `altseason`, `binance chain`, `token launch` | Misaligned with Bitcoin-only values. Apply score penalty. Do not exclude — they may be converts, but deprioritise. |
| **UNKNOWN** | bio is empty and handle yields no segment keywords | Cannot classify. Low-information node. |

### 1.2 Multi-label resolution
Run every segment's detection rule independently. A user matching BITCOINER + DEVELOPER + AFRICAN_TECH gets all three tags. The `SHITCOINER` flag is a **penalty modifier**, not a segment — it reduces the bio-signal score of whatever other segments matched (see §2).

### 1.3 Segment hierarchy for prioritisation
When a user matches multiple segments, the **primary segment** is the first matching row in this order:
1. AFRICAN_TECH (geo-priority)
2. BITCOINER (mission-core)
3. PRIVACY_SOVEREIGNTY (product-fit)
4. DEVELOPER (build-capacity)
5. ENTREPRENEUR (revenue-capacity)
6. NGO_DEV_SECTOR (grant/community-capacity)
7. MEDIA_PR (amplification)
8. VENDOR_ORG (b2b)
9. UNKNOWN

This ordering reflects Nikolai's stated geo-priority (Namibia > Kenya > SA) and mission (Bitcoin > sovereignty > builders > revenue).

---

## 2. INFLUENCE / VALUE SCORING

### 2.1 Formula

```
influence_score(u) = (
    W_PR  * pagerank_norm(u) +
    W_BC  * betweenness_norm(u) +
    W_MUT * int(is_mutual(u)) +
    W_VER * int(is_verified(u)) +
    W_BIO * bio_signal(u) +
    W_GEO * geo_score(u)
)
```

### 2.2 Weights and rationale

| Component | Weight | Why |
|-----------|--------|-----|
| `W_PR` — PageRank (normalized) | **0.20** | PageRank on the directed star measures who is followed by well-followed people within Nikolai's orbit. Works at 1-hop. |
| `W_BC` — Betweenness centrality (normalized) | **0.15** | **DORMANT at 1-hop** (zero for all leaves). Reserved for post-2-hop enrichment. When activated, it identifies bridges between otherwise-disconnected communities — the highest-leverage nodes for network growth. |
| `W_MUT` — Mutual status | **0.25** | The strongest available graph signal of active trust. A mutual is a two-way relationship; everything else is one-directional. Highest weight because trust is the substrate of both monetisation and community. |
| `W_VER` — Verified | **0.10** | Cheap external credibility signal. Does not guarantee alignment but correlates with real-world identity and audience. |
| `W_BIO` — Bio signal | **0.20** | The workhorse. We lack follower counts and location, so bio content carries alignment-detection. Segment-derived, mission-weighted. |
| `W_GEO` — Geo-commercial | **0.10** | Derived from bio keyword matching. Namibia (1.0) > Kenya (0.8) > SA (0.6) > other Africa (0.4) > non-Africa (0.0). |

Total: **1.00**

### 2.3 Component definitions

#### pagerank_norm(u)
```python
G = nx.DiGraph()
# edge OKIN_17 -> u  if Nikolai follows u
# edge u -> OKIN_17  if u follows Nikolai
pagerank = nx.pagerank(G, alpha=0.85)
pr_max = max(pagerank.values())
pagerank_norm(u) = pagerank[u] / pr_max
```

#### betweenness_norm(u)
```python
G_undirected = G.to_undirected()
betweenness = nx.betweenness_centrality(G_undirected, normalized=True)
bc_max = max(betweenness.values()) if max(betweenness.values()) > 0 else 1.0
betweenness_norm(u) = betweenness[u] / bc_max
```
> **Note:** At 1-hop, `betweenness` is 0 for all non-OKIN_17 nodes. This weight is forward-looking. After 2-hop enrichment it becomes one of the most valuable components.

#### bio_signal(u)
```python
def bio_signal(bio: str, handle: str) -> float:
    bio_l = bio.lower()
    handle_l = handle.lower()
    score = 0.0

    # --- Core alignment (mission-critical) ---
    if any(kw in bio_l for kw in ['bitcoin','btc','₿','satoshi','sats','node runner','self-custody','nostr','lightning','hardware wallet','bitcoin maxi','bitcoin minimalist','bip','taproot']):
        score += 0.40
    if any(kw in bio_l for kw in ['kenya','kibera','africa','namibia','south africa','african','windhoek','nairobi','johannesburg','cape town','lagos','nigeria','ghana','tanzania','uganda','rwanda','ethiopia']):
        score += 0.30
    if any(kw in bio_l for kw in ['privacy','sovereignty','freedom tech','self-custody','cypherpunk','encryption','tor','tails','vpn','opsec','digital rights']):
        score += 0.30

    # --- Supporting alignment (capped at 0.30) ---
    supporting = 0.0
    if any(kw in bio_l for kw in ['engineer','developer','python','rust','golang','building ','cto','programmer','open source','oss','github','firmware','protocol']):
        supporting += 0.20
    if any(kw in bio_l for kw in ['entrepreneur','founder','startup','ceo','co-founder','venture','investor','angel']):
        supporting += 0.15
    if any(kw in bio_l for kw in ['civil society','community','outreach','non-profit','ngo','empower','orphan','street kids','human rights','charity','foundation']):
        supporting += 0.15
    score += min(supporting, 0.30)

    # --- Peripheral alignment (capped at 0.15) ---
    peripheral = 0.0
    if any(kw in bio_l for kw in ['pr ','author','speaker','journalist','reporter','media','consultant','podcast','conference','meetup organizer']):
        peripheral += 0.10
    if any(kw in bio_l for kw in ['trezor','sparrow','liana','seedsigner','ledger','coldcard','blockstream','foundation','conference','merchandise','brand','company']):
        peripheral += 0.10
    score += min(peripheral, 0.15)

    # --- Handle signal (for empty-bio accounts) ---
    if 'btc' in handle_l or 'bitcoin' in handle_l or handle_l.endswith('sat') or handle_l.endswith('sats'):
        score += 0.30
    if 'crypto' in handle_l or 'blockchain' in handle_l:
        score += 0.15

    # --- Shitcoiner penalty ---
    if any(kw in bio_l for kw in ['ethereum','eth ','solana','altcoin','shitcoin','memecoin','airdrop','altseason']):
        score *= 0.5

    return min(score, 1.0)
```

#### geo_score(u)
```python
def geo_score(bio: str) -> float:
    bio_l = bio.lower()
    if any(kw in bio_l for kw in ['namibia','windhoek','swakopmund','walvis bay','luderitz']):
        return 1.0
    if any(kw in bio_l for kw in ['kenya','kibera','nairobi','mombasa','kisumu']):
        return 0.8
    if any(kw in bio_l for kw in ['south africa','johannesburg','cape town','durban','pretoria','port elizabeth','bloemfontein']):
        return 0.6
    if any(kw in bio_l for kw in ['africa','african','nigeria','ghana','tanzania','uganda','rwanda','ethiopia','senegal','cameroon','kinshasa','congo','zambia','zimbabwe','botswana','mozambique','angola','malawi']):
        return 0.4
    return 0.0
```

### 2.4 Why this weighting works without follower counts

We lack the single most common influence signal (follower count). We compensate with:
- **Mutual status** (0.25): a direct trust signal that does not require counts.
- **PageRank** (0.20): structural importance within the extracted graph — still meaningful at 1-hop because it captures "who is followed by people Nikolai follows" and "who follows people who follow Nikolai" transitively.
- **Bio signal** (0.20): replaces interest/engagement data with declared-identity data.
- **Geo** (0.10) + **Verified** (0.10): cheap tiebreakers.

When per-user follower counts are later enriched (see §6), the `W_VER` and `W_GEO` weights should be partially reallocated to a new `W_FC` (follower-count) component.

---

## 3. RELATIONSHIP TIERS

Tiers are defined by the **asymmetry** of the connection. The asymmetries are where the actionable value lives — not in the mutuals (those are already healthy) but in the one-directional edges that represent untapped potential or unexamined assumptions.

### 3.1 Tier definitions

| Tier | Detection rule | Meaning | Action |
|------|---------------|---------|--------|
| **T1 — INNER CIRCLE** | `is_mutual == True` AND `influence_score >= P80` | Core collaborators. High-value two-way trust. | Co-create. Revenue-share. Invite to private channels. Ask what they need. Prioritize their needs over yours. |
| **T2 — WARM** | `is_mutual == True` AND `influence_score < P80` | Active mutuals, not yet differentiated. | Nurture. Engage weekly. Convert to T1 by adding value first. Ask what they're building. |
| **T3 — ASPIRATIONAL** | `is_following == True` AND `is_follower == False` | Nikolai follows them, they don't follow back. He has curated these; they signal who he admires or needs something from. | Audit: why does Nikolai follow them? If high-value, engage publicly to earn follow-back. If low-value, unfollow to reduce noise. These are growth targets. |
| **T4 — POTENTIAL** | `is_follower == True` AND `is_following == False` | They follow Nikolai; he hasn't reciprocated. They chose him. | Evaluate. High-influence ones should be considered for reciprocation. These are the audience, customer base, and community pool. |
| **T5 — VIP OUTREACH** (T4 sub-tier) | `is_follower == True` AND `is_following == False` AND `influence_score >= P90` | VIPs who chose Nikolai but he hasn't followed back. | Do NOT monetize aggressively. Follow back if alignment is genuine. First add value — they raised a hand. |

`P80` = 80th percentile of `influence_score` across all nodes. `P90` = 90th percentile.

### 3.2 What the asymmetries mean

**He follows, they don't (T3):**
- Nikolai has actively chosen these accounts. They represent aspiration, curiosity, or need.
- Value signal: if they're high-influence and Bitcoin/African aligned, they are worth earning a mutual with.
- Danger signal: if they're low-influence, empty-bio, or shitcoin-aligned, they are noise Nikolai should prune.

**They follow, he doesn't (T4):**
- These accounts chose Nikolai without reciprocation. They are the demand side.
- Value signal: audience, customers, community members, potential revenue.
- Sub-segment: T5 (VIP OUTREIGHT) are the highest-value demand — influential people who raised a hand. Nikolai should notice them.

### 3.3 Tier transition goals
- T3 -> T2: earn the follow-back through public engagement and value delivery.
- T2 -> T1: deepen through direct collaboration and shared work.
- T4 -> T5 -> T2: evaluate VIPs, reciprocate where genuine, convert to mutual through engagement.
- T4 -> T3 is impossible (would mean Nikolai unfollowing and them following — no action path).

---

## 4. OPPORTUNITY MAPS

Two maps, honestly distinct. Monetisation is about revenue to Nikolai. Community is about collective benefit to the participants. The boundary between them is enforced.

### 4.1 MONETISATION MAP

Revenue mechanisms realistic for a Namibian freedom-tech builder. Geo-weighting applied: **Namibia (1.0) > Kenya (0.8) > South Africa (0.6) > other Africa (0.4) > global (0.1)**.

| Mechanism | Target segments | Target geo | Product / offer | Why realistic |
|-----------|----------------|------------|-----------------|---------------|
| **HISHE licenses / sales** | BITCOINER, PRIVACY_SOVEREIGNTY, DEVELOPER | Global, Africa prioritized | Sovereign OS with Bitcoin stack | They already run Bitcoin nodes/wallets. HISHE is their daily driver. |
| **HISHE enterprise / NGO licenses** | NGO_DEV_SECTOR, ENTREPRENEUR | Africa prioritized | Multi-seat deployment, training, support | NGOs need data-sovereignty. Grants cover tooling. |
| **Consulting / advisory** | ENTREPRENEUR, NGO_DEV_SECTOR, VENDOR_ORG | Namibia > Kenya > SA | Security audits, self-custody setup, regulatory navigation | Nikolai's 9-agent team is a credible delivery engine. |
| **Speaking / training** | MEDIA_PR, VENDOR_ORG, NGO_DEV_SECTOR | Global, Africa prioritized | Keynotes, workshops, HISHE bootcamps | Conferences pay. Meetups lead to consulting gigs. |
| **Hardware wallet affiliate** | BITCOINER, VENDOR_ORG | Global | Trezor, SeedSigner, Sparrow, Liana referral fees | VENDOR_ORG segment handles are aligned. Affiliate revenue is low-friction. |
| **B2B dev services** | DEVELOPER, ENTREPRENEUR | Namibia > Kenya > SA | Contract development for freedom-tech clients | Developers who are also entrepreneurs buy dev work. |
| **Bitcoin circular-economy pilots** | BITCOINER + AFRICAN_TECH | Kenya, Namibia | Merchant onboarding, Lightning HISHE nodes | Afribit Kibera-style projects. Revenue via grants + training fees. |

**Geo scoring is multiplicative:** a Namibian Bitcoiner-entrepreneur scores higher than a US one for monetisation because they're in Nikolai's priority zone and reachable for in-person work.

### 4.2 COMMUNITY PROJECT MAP

Collective initiatives that benefit the participants, not just Nikolai. Must be genuinely win-win-win.

| Project | Target segments | Target geo | What participants get | What Nikolai gets |
|---------|----------------|------------|----------------------|-------------------|
| **HISHE local user groups** | BITCOINER, DEVELOPER, AFRICAN_TECH | Namibia, Kenya, SA | Peer support, local meetups, shared learning | Distribution channel, feedback loop, brand advocates |
| **Bitcoin circular-economy pilots** | BITCOINER + AFRICAN_TECH | Kenya (Kibera), Namibia | Merchant income, financial inclusion, skills | Case studies, grant co-authorship, HISHE deployment data |
| **Developer bounties for HISHE** | DEVELOPER | Global | Paid work, open-source reputation, portfolio | Features shipped, community-owned code |
| **Privacy-tool workshops for civil society** | NGO_DEV_SECTOR, PRIVACY_SOVEREIGNTY | Africa | Training, tooling, digital safety | Grant co-applicants, impact stories, HISHE adoption |
| **Sovereign-tech meetups** | All segments | Namibia > Kenya > SA | Networking, knowledge, collaboration | Network density, deal flow, talent pipeline |
| **Open-source HISHE core sprints** | DEVELOPER, BITCOINER | Global | Skill-building, mentorship, visible contributions | Code contributions, bug fixes, new maintainers |

### 4.3 THE BOUNDARY — what NOT to monetize

| Do NOT monetize | Why |
|-----------------|-----|
| **Journalists / media (MEDIA_PR)** | They are amplifiers, not buyers. Selling to them burns trust and kills the amplification channel. Serve them; they broadcast. |
| **Fellow developers who are peers (DEVELOPER, non-entrepreneur)** | Collaborate, don't extract. Peers become co-maintainers or competitors — either way, extraction backfires. |
| **NGOs via hard sell** | NGOs have grant cycles and mission mandates. Serve them through community projects; they become institutional buyers on their own timeline. |
| **Empty-bio + low-connectivity followers** | Likely bots or dormant accounts. Not community. Do not count them as audience. |
| **Shitcoiners via HISHE** | Misaligned values. HISHE is Bitcoin-only. Selling to shitcoiners invites feature-scope contamination. They may convert to Bitcoin-only over time — let that happen organically. |
| **Any mutual via aggressive outbound** | Mutuals are trust capital. Monetize only when they ask or when the value exchange is obvious and welcome. |

---

## 5. GRAPH QUESTIONS WORTH ASKING

Each question is stated in plain English, followed by the graph metric that answers it. Split into **available now** (1-hop) and **available after 2-hop enrichment**.

### 5.1 Available now (1-hop graph)

| # | Question | Metric | Code |
|---|----------|--------|------|
| Q1 | Who are my most structurally important connections within my own orbit? | PageRank (directed) | `nx.pagerank(G)` |
| Q2 | Who has the most followers among people I follow? (proxy: who do my mutuals also follow?) | Out-degree of mutuals in the following-list subgraph | `G.out_degree(mutuals)` |
| Q3 | What is my follow-back rate? | `mutual_count / following_count` | simple ratio |
| Q4 | Who follows me that I don't follow back, ranked by influence? | Filter `is_follower AND NOT is_following`, sort by `influence_score` | filter + sort |
| Q5 | Who do I follow that doesn't follow me back, ranked by influence? | Filter `is_following AND NOT is_follower`, sort by `influence_score` | filter + sort |
| Q6 | Are my mutuals more likely to be verified than my one-way connections? | `P(verified | mutual)` vs `P(verified | not mutual)` | conditional frequency |
| Q7 | Which segments are over/under-represented among mutuals vs one-way? | Segment distribution by tier | groupby + count |

### 5.2 Available after 2-hop enrichment

| # | Question | Metric | Code |
|---|----------|--------|------|
| Q8 | Who bridges otherwise-disconnected communities in my extended network? | Betweenness centrality (undirected) | `nx.betweenness_centrality(G.to_undirected())` |
| Q9 | What natural communities exist among my connections? | Louvain community detection / greedy modularity | `community_louvain.best_partition(G_undirected)` or `nx.community.greedy_modularity_communities` |
| Q10 | Which communities am I adjacent to but not embedded in? | Community assignment of OKIN_17's neighbors vs OKIN_17's own community membership | compare community labels |
| Q11 | Who are the gatekeepers of the Kenyan Bitcoin community? | Highest betweenness within the Kenyan-tagged subgraph | subgraph + betweenness |
| Q12 | What is the shortest path between any two of my connections? | Shortest path length | `nx.shortest_path_length(G, u, v)` |
| Q13 | Where are the structural holes I could fill? | Constraint / Burt's structural holes | `nx.constraint(G_undirected)` |
| Q14 | How dense is my network overall, and where are the sparse regions? | Network density, clustering coefficient | `nx.density(G)`, `nx.average_clustering(G_undirected)` |
| Q15 | Who should I introduce to whom to strengthen the network? | Shortest-path-betweenness of non-connected pairs | iterate over disconnected high-value pairs |

### 5.3 Priority order for 2-hop extraction
If extracting 2-hop data (followers of followers), do it in this order:
1. **Mutuals first** (282 of them) — their followers are the highest-probability community members.
2. **T5 VIP OUTREACH** — their followers reveal the communities Nikolai is adjacent to.
3. **T3 ASPIRATIONAL** — their followers reveal aspirational communities.

Cap at ~50-100 profiles per tier to stay within rate-limit safety. Use >=3s delay between profile visits.

---

## 6. WHAT WE CANNOT ANSWER YET

### 6.1 Missing data, ranked by value

| Missing data | What it would unlock | Worth the cost? |
|--------------|---------------------|-----------------|
| **Per-user follower counts** | True influence ranking. Currently we use PageRank as a proxy, but follower count is the standard social-proof signal. | **Yes, for top-N only.** Extract for the top 50-100 connections by influence_score. Use opt-in `--enrich N` with >=3s delay. Risk: rate-limiting. Do NOT visit 1,600 profiles. |
| **Follower-of-follower (2-hop) edges** | Community detection, betweenness, bridges, structural holes. Unlocks Q8-Q15. The single highest-value missing dataset. | **Yes, bounded.** Extract followers of the top 50 mutuals + top 20 VIPs. ~5-10k nodes, still trivial for NetworkX. Use delays. |
| **Location data** | Geo-priority monetisation. Currently inferred from bio keywords (noisy, incomplete). | **Partially.** Bio inference covers ~30-40% of users. Full enrichment not worth the scraping cost. Accept the noise. |
| **Tweet content / history** | Interest mapping beyond bio. Engagement patterns. Bot detection. | **No, not yet.** Requires API or heavy scraping. Risk/reward is poor at this stage. Revisit when the 1-hop + 2-hop pipeline is stable. |
| **Account age / activity frequency** | Bot filtering. Dormant-account detection. | **Medium value.** Can partially infer from bio freshness and mutual status. Not worth dedicated scraping. |
| **Engagement data (likes, replies, retweets)** | Relationship-strength signal. Who actually interacts vs. silently follows. | **High value but high risk.** Requires API or aggressive scraping. Defer until follower-count enrichment proves safe. |

### 6.2 Highest-value next extraction
**2-hop edges from the top 50 mutuals.** This is the single most valuable dataset to add because it unlocks community detection and betweenness — the metrics that reveal bridges, gatekeepers, and structural holes. Without it, the graph is a star and topology-based analysis is dormant.

### 6.3 What we should NOT try to answer yet
- "Who is the most influential person in my network?" — cannot answer without follower counts or 2-hop edges. PageRank is a proxy, not a substitute.
- "What is the full community structure?" — cannot answer without 2-hop edges.
- "Who should I pitch first?" — can partially answer with influence_score + segment + geo, but true conversion probability requires engagement data we don't have.
- "Is this account a bot?" — cannot reliably answer without activity data. Use heuristics: empty bio + no verified + low influence_score + no mutual = likely low-value, but not necessarily a bot.

---

## APPENDIX A — implementable scoring function (for Doom)

```python
import networkx as nx
import json

# --- Load data ---
with open('data/x_graph_v3.json') as f:
    data = json.load(f)

# --- Build directed graph ---
G = nx.DiGraph()
main = data['username']  # "OKIN_17"
G.add_node(main)

for u in data['followers']:
    handle = u['handle']
    G.add_node(handle)
    G.add_edge(handle, main)  # they follow Nikolai
    if u.get('is_following', False):  # mutual
        G.add_edge(main, handle)

for u in data['following']:
    handle = u['handle']
    G.add_node(handle)
    G.add_edge(main, handle)  # Nikolai follows them
    if u.get('is_follower', False):  # mutual (should already have edge)
        G.add_edge(handle, main)

# --- Graph metrics ---
pagerank = nx.pagerank(G, alpha=0.85)
pr_max = max(pagerank.values()) if pagerank else 1.0

G_undirected = G.to_undirected()
betweenness = nx.betweenness_centrality(G_undirected, normalized=True)
bc_max = max(betweenness.values()) if betweenness else 1.0

# --- Segment keywords ---
SEG_BITCOINER = ['bitcoin','btc','₿','satoshi','sats','node runner',
    'self-custody','nostr','lightning','hardware wallet','bitcoin maxi',
    'bitcoin minimalist','bip','taproot','ordinals','inscriptions']
SEG_AFRICAN = ['kenya','kibera','africa','namibia','south africa',
    'african','windhoek','nairobi','johannesburg','cape town','durban',
    'lagos','nigeria','ghana','tanzania','uganda','rwanda','ethiopia',
    'senegal','cameroon','kinshasa','congo','zambia','zimbabwe',
    'botswana','mozambique','angola','malawi']
SEG_DEVELOPER = ['engineer','developer','dev ','python','rust','golang',
    'javascript','full stack','backend','frontend','firmware','protocol',
    'building ','cto','programmer','open source','oss','github','gitlab']
SEG_PRIVACY = ['privacy','sovereignty','freedom tech','self-custody',
    'cypherpunk','encryption','tor','tails','vpn','opsec','digital rights']
SEG_ENTREPRENEUR = ['entrepreneur','founder','startup','ceo','co-founder',
    'venture','investor','angel']
SEG_NGO = ['civil society','community','outreach','non-profit','ngo',
    'empower','orphan','street kids','human rights','charity','foundation']
SEG_MEDIA = ['pr ','author','speaker','journalist','reporter','media',
    'consultant','podcast','conference','meetup organizer']
SEG_VENDOR = ['trezor','sparrow','liana','seedsigner','ledger','coldcard',
    'blockstream','foundation','merchandise','brand','company']
SEG_SHITCOIN = ['ethereum','eth ','solana','altcoin','shitcoin','memecoin',
    'airdrop','altseason']

GEO_NAMIBIA = ['namibia','windhoek','swakopmund','walvis bay','luderitz']
GEO_KENYA = ['kenya','kibera','nairobi','mombasa','kisumu']
GEO_SA = ['south africa','johannesburg','cape town','durban','pretoria',
    'port elizabeth','bloemfontein']
GEO_AFRICA = ['africa','african','nigeria','ghana','tanzania','uganda',
    'rwanda','ethiopia','senegal','cameroon','kinshasa','congo','zambia',
    'zimbabwe','botswana','mozambique','angola','malawi']

def any_kw(text, kws):
    return any(kw in text for kw in kws)

def bio_signal(bio, handle):
    bio_l = bio.lower()
    handle_l = handle.lower()
    score = 0.0
    if any_kw(bio_l, SEG_BITCOINER): score += 0.40
    if any_kw(bio_l, SEG_AFRICAN):   score += 0.30
    if any_kw(bio_l, SEG_PRIVACY):   score += 0.30
    sup = 0.0
    if any_kw(bio_l, SEG_DEVELOPER):    sup += 0.20
    if any_kw(bio_l, SEG_ENTREPRENEUR): sup += 0.15
    if any_kw(bio_l, SEG_NGO):          sup += 0.15
    score += min(sup, 0.30)
    per = 0.0
    if any_kw(bio_l, SEG_MEDIA):   per += 0.10
    if any_kw(bio_l, SEG_VENDOR):  per += 0.10
    score += min(per, 0.15)
    if 'btc' in handle_l or 'bitcoin' in handle_l or handle_l.endswith('sat') or handle_l.endswith('sats'):
        score += 0.30
    if 'crypto' in handle_l or 'blockchain' in handle_l:
        score += 0.15
    if any_kw(bio_l, SEG_SHITCOIN):
        score *= 0.5
    return min(score, 1.0)

def geo_score(bio):
    bio_l = bio.lower()
    if any_kw(bio_l, GEO_NAMIBIA): return 1.0
    if any_kw(bio_l, GEO_KENYA):   return 0.8
    if any_kw(bio_l, GEO_SA):      return 0.6
    if any_kw(bio_l, GEO_AFRICA):  return 0.4
    return 0.0

# --- Score every node ---
W_PR, W_BC, W_MUT, W_VER, W_BIO, W_GEO = 0.20, 0.15, 0.25, 0.10, 0.20, 0.10

# Build lookup from data
node_data = {}
for u in data['followers'] + data['following']:
    h = u['handle']
    if h not in node_data:
        node_data[h] = {
            'bio': u.get('bio', ''),
            'verified': u.get('verified', False),
            'is_follower': u.get('is_follower', u in data['followers']),
            'is_following': u.get('is_following', u in data['following']),
        }

results = []
for handle, info in node_data.items():
    pr_n = pagerank.get(handle, 0.0) / pr_max
    bc_n = betweenness.get(handle, 0.0) / bc_max
    mut = 1.0 if (info['is_follower'] and info['is_following']) else 0.0
    ver = 1.0 if info['verified'] else 0.0
    bio = bio_signal(info['bio'], handle)
    geo = geo_score(info['bio'])
    score = W_PR*pr_n + W_BC*bc_n + W_MUT*mut + W_VER*ver + W_BIO*bio + W_GEO*geo
    results.append({
        'handle': handle,
        'influence_score': round(score, 4),
        'components': {
            'pagerank_norm': round(pr_n, 4),
            'betweenness_norm': round(bc_n, 4),
            'mutual': mut,
            'verified': ver,
            'bio_signal': round(bio, 4),
            'geo_score': round(geo, 4),
        },
        'is_follower': info['is_follower'],
        'is_following': info['is_following'],
        'is_mutual': mut == 1.0,
    })

# --- Assign tiers ---
scores = sorted([r['influence_score'] for r in results])
p80 = scores[int(len(scores) * 0.8)] if scores else 0.0
p90 = scores[int(len(scores) * 0.9)] if scores else 0.0

for r in results:
    if r['is_mutual'] and r['influence_score'] >= p80:
        r['tier'] = 'T1_INNER_CIRCLE'
    elif r['is_mutual']:
        r['tier'] = 'T2_WARM'
    elif r['is_following'] and not r['is_follower']:
        r['tier'] = 'T3_ASPIRATIONAL'
    elif r['is_follower'] and not r['is_following']:
        if r['influence_score'] >= p90:
            r['tier'] = 'T5_VIP_OUTREACH'
        else:
            r['tier'] = 'T4_POTENTIAL'
    else:
        r['tier'] = 'UNKNOWN'

results.sort(key=lambda x: x['influence_score'], reverse=True)

with open('data/x_crm_scored.json', 'w') as f:
    json.dump({'generated_at': '2026-08-18', 'p80': p80, 'p90': p90,
               'nodes': results}, f, indent=2)
```

---

## APPENDIX B — riddles (critical unknowns)

1. **Who are the 20 nodes that, if removed, fragment my mutual cluster the most?** (Answer requires 2-hop edges + articulation-point analysis.)
2. **Which of my T4 POTENTIAL followers are followed by my T1 INNER CIRCLE mutuals?** (Answer requires 2-hop edges. These are warm introductions waiting to happen.)
3. **What is the conversion rate from T4 -> T2 over time, and what predicts it?** (Answer requires longitudinal re-extraction. Currently we have a snapshot, not a time series.)
4. **Which segment has the highest monetisation yield per contact?** (Answer requires tracking actual revenue per segment over time. Currently we have no revenue data.)
5. **Who in my network is a bot, and who is merely quiet?** (Answer requires activity data we do not have. The empty-bio population is ~unknown% of the dataset — we cannot distinguish low-signal humans from bots without tweet history.)

---

*End of methodology. Every rule above is executable against the fields listed in §0. No invented data. No altcoins. No paid APIs.*
