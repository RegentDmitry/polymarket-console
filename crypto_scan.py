import json, urllib.request, re, sys, math
from scipy.stats import norm

# Deribit data (from deribit_compare.py output)
S = 70381  # Deribit index
IV = 0.519
T_annual = 307/365  # days to Dec 25 2026

def touch_prob_above(S, K, sigma, T, mu=0):
    if K <= S:
        return 1.0
    ln_ratio = math.log(K / S)
    sqrt_T = math.sqrt(T)
    sigma_sqrt = sigma * sqrt_T
    d1 = (mu * T - ln_ratio) / sigma_sqrt
    d2 = (mu * T + ln_ratio) / sigma_sqrt
    if mu == 0:
        return 2 * (1 - norm.cdf(ln_ratio / sigma_sqrt))
    drift_factor = 2 * mu / (sigma ** 2)
    return norm.cdf(d1) + math.exp(drift_factor * ln_ratio) * norm.cdf(d2) if drift_factor * ln_ratio < 500 else norm.cdf(d1)

def touch_prob_below(S, K, sigma, T, mu=0):
    if K >= S:
        return 1.0
    ln_ratio = math.log(S / K)
    sqrt_T = math.sqrt(T)
    sigma_sqrt = sigma * sqrt_T
    d1 = (-mu * T - ln_ratio) / sigma_sqrt
    d2 = (-mu * T + ln_ratio) / sigma_sqrt
    if mu == 0:
        return 2 * (1 - norm.cdf(ln_ratio / sigma_sqrt))
    drift_factor = -2 * mu / (sigma ** 2)
    return norm.cdf(d1) + math.exp(drift_factor * ln_ratio) * norm.cdf(d2) if drift_factor * ln_ratio < 500 else norm.cdf(d1)

# Event slugs to scan
slugs = [
    'what-price-will-bitcoin-hit-before-2027',
    'what-price-will-bitcoin-hit-in-march-2026',
    'what-price-will-ethereum-hit-before-2027',
    'what-price-will-ethereum-hit-in-march-2026',
]

# ETH data
ETH_S = 2800  # approx ETH price
ETH_IV = 0.70  # ETH IV typically higher

results = []

for slug in slugs:
    try:
        url = f'https://gamma-api.polymarket.com/events?slug={slug}'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        if not data:
            continue
        event = data[0]
        markets = event.get('markets', [])
        for m in markets:
            if m.get('closed') or not m.get('active'):
                continue
            q = m.get('question', '')
            outcomes = m.get('outcomePrices', '[]')
            try:
                prices = json.loads(outcomes)
                yes_price = float(prices[0])
            except:
                continue
            
            # Parse strike and direction
            dollar_match = re.search(r'\$([0-9,]+)', q)
            if not dollar_match:
                nums = re.findall(r'[0-9,]+', q)
                strike = None
                for n in nums:
                    n_clean = n.replace(',', '')
                    if n_clean.isdigit() and int(n_clean) > 100:
                        val = int(n_clean)
                        if val > 2020 and val < 2030:
                            continue
                        strike = val
                        break
                if not strike:
                    continue
            else:
                strike = int(dollar_match.group(1).replace(',', ''))
            
            is_eth = 'ethereum' in slug or 'eth' in slug.lower()
            is_reach = 'reach' in q.lower() or 'hit' in q.lower() or q.lower().startswith('\u2191') or '>' in q
            is_dip = 'dip' in q.lower() or 'drop' in q.lower() or q.lower().startswith('\u2193') or '<' in q
            
            if not is_reach and not is_dip:
                if strike > (ETH_S if is_eth else S):
                    is_reach = True
                else:
                    is_dip = True
            
            spot = ETH_S if is_eth else S
            iv = ETH_IV if is_eth else IV
            
            if 'march-2026' in slug:
                T = 37/365
            elif '2027' in slug or '2025' in slug:
                T = T_annual
            else:
                T = T_annual
            
            drift = 0.27
            if is_reach or strike > spot:
                tp_drift = touch_prob_above(spot, strike, iv, T, mu=drift)
                tp_zero = touch_prob_above(spot, strike, iv, T, mu=0)
                side = 'YES'
                edge_drift = tp_drift - yes_price
                edge_zero = tp_zero - yes_price
            else:
                tp_drift = touch_prob_below(spot, strike, iv, T, mu=drift)
                tp_zero = touch_prob_below(spot, strike, iv, T, mu=0)
                no_price = 1 - yes_price
                side = 'NO'
                edge_drift = (1 - tp_drift) - no_price
                edge_zero = (1 - tp_zero) - no_price
            
            currency = 'ETH' if is_eth else 'BTC'
            direction = '\u2191' if (is_reach or strike > spot) else '\u2193'
            
            results.append({
                'market': f'{currency} {direction} ${strike:,}',
                'slug_short': slug.split('-')[-1] if 'march' in slug else ('annual' if '2027' in slug else slug.split('-')[-1]),
                'yes_price': yes_price,
                'touch_d0': tp_zero,
                'touch_d27': tp_drift,
                'side': side,
                'edge_d0': edge_zero,
                'edge_d27': edge_drift,
                'strike': strike,
                'question': q[:50],
                'period': 'March' if 'march' in slug else 'Annual',
                'token_id': m.get('clobTokenIds', ['',''])[0] if side == 'YES' else m.get('clobTokenIds', ['',''])[1],
            })
    except Exception as e:
        print(f'Error loading {slug}: {e}', file=sys.stderr)

results.sort(key=lambda x: x['edge_d27'], reverse=True)

print(f'BTC Spot: ${68033:,} | Deribit: ${S:,} | IV: {IV*100:.1f}%')
print(f'ETH Spot: ${ETH_S:,} | IV: {ETH_IV*100:.1f}%')
print(f'Найдено {len(results)} активных рынков')
print()
header = f'{"Рынок":<25} {"Период":<8} {"Сторона":<7} {"PM":<7} {"Touch d=0":<10} {"Touch d=27":<11} {"Edge d=0":<10} {"Edge d=27":<10}'
print(header)
print('-' * 95)
for r in results:
    pm_str = f'{r["yes_price"]*100:.0f}c' if r['side'] == 'YES' else f'{(1-r["yes_price"])*100:.0f}c'
    print(f'{r["market"]:<25} {r["period"]:<8} {r["side"]:<7} {pm_str:<7} {r["touch_d0"]*100:>6.1f}%   {r["touch_d27"]*100:>6.1f}%    {r["edge_d0"]*100:>+6.1f}%   {r["edge_d27"]*100:>+6.1f}%')
