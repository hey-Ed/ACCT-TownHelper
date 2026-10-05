"""ACCT TownHelper - acre recognition engine for Animal Crossing: City Folk town maps.

The acre catalogue is built from the CF TownHelper document by frobuddyharry."""
import json, os, sys, hashlib
import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

LIGHT, WATER, SAND, GREY, BRIDGE, LINE, ICON, DARK = 0, 1, 2, 3, 4, 5, 6, 7
SIZE = 910
ACRE = SIZE // 5
BUILDING_LABELS = {"town gate": "gate", "museum": "museum", "Nooks": "nook",
                   "town hall": "townhall", "ables": "able"}

# ---------------------------------------------------------------- capture
# Reference colours (grass / water) of the captures the classifier was tuned on
REF_GRASS = np.array([71.0, 157.0, 30.2])
REF_WATER = np.array([9.5, 90.4, 180.5])

def normalize_colors(a):
    """Maps the capture colours onto the reference ones, using grass and water
    as anchors, so captures with other colour settings still classify well."""
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    grass = (g > r + 30) & (g > b + 30)
    water = (b > g + 40) & (b > r + 80)
    if grass.sum() < 2000 or water.sum() < 500:
        return a
    sg, sw = np.median(a[grass], 0), np.median(a[water], 0)
    if np.abs(sg - REF_GRASS).max() < 12 and np.abs(sw - REF_WATER).max() < 12:
        return a   # already close to the reference: leave untouched
    out = a.astype(float)
    for ch in range(3):
        d = sw[ch] - sg[ch]
        if abs(d) > 30:
            out[..., ch] = (out[..., ch] - sg[ch]) / d * (REF_WATER[ch] - REF_GRASS[ch]) + REF_GRASS[ch]
        else:
            out[..., ch] += REF_GRASS[ch] - sg[ch]
    return np.clip(out, 0, 255).astype(int)

def classify_capture(img):
    a = normalize_colors(np.array(img.filter(ImageFilter.MedianFilter(3))).astype(int))
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    c = np.where(g < grass_threshold(r, g, b), DARK, LIGHT)
    c[(r > 90) & (g < 115) & (b < 60)] = LINE
    c[(b > 120) & (g < 140) & (r < 70)] = WATER
    c[(r > 140) & (g > 170) & (b > 45) & (b < 150)] = SAND
    c[(abs(r - g) < 30) & (r > 120) & (b > 100) & (abs(g - b) < 70)] = GREY
    c[(r > 170) & (g > 90) & (g < 160) & (b < 130)] = BRIDGE
    c[house_mask(a) | building_mask(a) | ((b > 120) & (r < 60) & (g < 70))] = ICON
    return c

def grass_threshold(r, g, b, default=163):
    """Split between upper (darker) and lower (lighter) grass.
    Computed per capture (Otsu) so other capture setups / colour settings work too."""
    grass = (g > r + 40) & (g > b + 60)
    vals = g[grass]
    if vals.size < 1000:
        return default
    hist = np.bincount(np.clip(vals, 0, 255), minlength=256).astype(float)
    total = hist.sum(); levels = np.arange(256)
    w0 = np.cumsum(hist); w1 = total - w0
    m0 = np.cumsum(hist * levels) / np.maximum(w0, 1)
    m1 = ((hist * levels).sum() - np.cumsum(hist * levels)) / np.maximum(w1, 1)
    between = w0 * w1 * (m0 - m1) ** 2
    t = int(between.argmax()) + 1
    # two real levels must be clearly separated, otherwise keep the default
    return t if abs(m1[t - 1] - m0[t - 1]) > 8 else default

def house_mask(a):
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return (b > 170) & (g > 150)

def building_mask(a):
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    yellow = (r > 150) & (g > 150) & (b < 90) & (r > b + 120)
    pink = (r > 140) & (b > 90) & (g < 150)
    return yellow | pink

def acre_grid(cls):
    """One full-resolution acre -> 48x48 grid of terrain classes."""
    H, W = cls.shape
    out = np.full((48, 48), LIGHT); valid = np.ones((48, 48), bool)
    ys = np.linspace(0, H, 49).astype(int); xs = np.linspace(0, W, 49).astype(int)
    for i in range(48):
        for j in range(48):
            blk = cls[ys[i]:ys[i+1], xs[j]:xs[j+1]].ravel()
            if (blk == ICON).mean() > 0.4: valid[i, j] = False; continue
            blk = blk[blk != ICON]
            cnt = np.bincount(blk, minlength=8); cnt[LINE] = 0
            out[i, j] = cnt.argmax()
    return out

def components(mask, min_area):
    lab, n = ndimage.label(ndimage.binary_closing(mask, iterations=3))
    res = []
    for k in range(1, n + 1):
        comp = lab == k
        if comp.sum() >= min_area:
            ys, xs = np.nonzero(comp)
            res.append((ys.mean(), xs.mean(), comp, ys, xs))
    return res

# ---------------------------------------------------------------- catalogue
def data_dir():
    base = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, 'townhelper_data')


class Catalog:
    def stack(self):
        V, owner = [], []
        for n, e in enumerate(self.entries):
            for v in e['variants']:
                V.append(v); owner.append(n)
        self.V = np.array(V, np.uint8); self.owner = np.array(owner)

    def __init__(self, folder):
        self.entries = json.load(open(os.path.join(folder, 'catalog.json')))
        for e in self.entries:
            e['twin'] = any(o is not e and o['sheet'] == e['sheet'] and o['label'] == e['label']
                            and o['bridge'] != e['bridge'] for o in self.entries)
        arr = np.load(os.path.join(folder, 'catalog.npz'))
        for n, e in enumerate(self.entries):
            mini = arr[f'mini_{n}'].astype(int)
            e['mini'] = mini.copy()
            mini[mini == BRIDGE] = WATER
            e['variants'] = variants(mini)
            # same look on the map: bridge / no-bridge twins share their samples
            e['look'] = (f"{e['sheet']}|{e['label']}" if e['twin']
                         else hashlib.md5(mini.tobytes()).hexdigest()[:12])
            e['tiles'] = arr[f'tiles_{n}'].astype(int)
            e['objs'] = arr[f'objs_{n}'].astype(bool)
            lab = e['label']
            e['building'] = BUILDING_LABELS.get(lab) if e['sheet'] == 'Buildings' else None
            e['lake'] = e['sheet'] == 'River StartLakes' and 'lake' in lab.lower()
            e['river_start'] = lab.lower().startswith('river start')
            e['beach'] = e['sheet'] == 'Beach'
            e['blank'] = e['id'] in (48, 49, 50)
            t = e['tiles']
            w = (t == WATER) | (t == BRIDGE)
            e['edges'] = {'N': w[0], 'S': w[-1], 'W': w[:, 0], 'E': w[:, -1]}
            e['name'] = f"{e['id']}" if e['id'] is not None else f"{lab} #{e['k'] + 1}"
        self.stack()

def variants(mg):
    d, l = (mg == DARK).mean(), (mg == LIGHT).mean()
    if d > 0.05 and l > 0.05:
        return [mg]
    a = mg.copy(); a[a == DARK] = LIGHT
    b = mg.copy(); b[b == LIGHT] = DARK
    return [a, b]

def shifted(mg, dy, dx):
    m = np.roll(np.roll(mg, dy, 0), dx, 1)
    v = np.ones(mg.shape, bool)
    if dy > 0: v[:dy] = False
    if dy < 0: v[dy:] = False
    if dx > 0: v[:, :dx] = False
    if dx < 0: v[:, dx:] = False
    return m, v

def score(cg, valid, mvars, detail=False, center=(0, 0), radius=1):
    best = 0; bm = bv = None; bs = center
    base = valid & (cg != LINE)
    cy, cx = center
    for mg in mvars:
        for dy in range(cy - radius, cy + radius + 1):
            for dx in range(cx - radius, cx + radius + 1):
                m, v = shifted(mg, dy, dx)
                v &= base & (m != LINE)
                if v.any():
                    sc = (cg == m)[v].mean()
                    if sc > best: best, bm, bv, bs = sc, m, v, (dy, dx)
    return (best, bm, bv, bs) if detail else best

def refine(cg, scores, fits, window=0.06, k=6, weight=0.1):
    k = min(k, len(fits))
    """Separates close candidates by looking ONLY at the pixels where they
    differ (e.g. the small bump that tells acres 21 and 22 apart)."""
    top = sorted(scores, key=scores.get, reverse=True)[:k]
    top = [n for n in top if scores[n] >= scores[top[0]] - window]
    if len(top) < 2: return scores
    adj = {n: 0.0 for n in top}
    for i, a in enumerate(top):
        for b in top[i+1:]:
            ma, va = fits[a][:2]; mb, vb = fits[b][:2]
            d = va & vb & (ma != mb)
            if d.sum() < 4: continue
            diff = ((cg == ma)[d].mean() - (cg == mb)[d].mean())
            adj[a] += diff; adj[b] -= diff
    out = dict(scores)
    for n in top: out[n] += weight * adj[n] / (len(top) - 1)
    return out

# ---------------------------------------------------------------- recognition
def capture_grids(img):
    """Cuts a capture into 25 grids of 48x48 (+ validity masks)."""
    img = img.convert('RGB').resize((SIZE, SIZE), Image.Resampling.LANCZOS)
    cls = classify_capture(img); cls[cls == BRIDGE] = WATER
    grids = {}
    ys = np.linspace(0, ACRE, 49).astype(int)
    for gy in range(5):
        for gx in range(5):
            sub = cls[gy*ACRE:(gy+1)*ACRE, gx*ACRE:(gx+1)*ACRE]
            icon = sub == ICON
            valid = np.array([[icon[ys[i]:ys[i+1], ys[j]:ys[j+1]].mean() <= 0.4 for j in range(48)] for i in range(48)])
            grids[(gy, gx)] = (acre_grid(sub), valid)
    return grids

def batch_score(cg, valid, V, center=(0, 0), radius=1):
    """Vectorised score() for all variants at once.
    Returns (best score, shift) per variant."""
    base = valid & (cg != LINE)
    best = np.zeros(len(V)); bshift = np.zeros((len(V), 2), int)
    cy, cx = center
    for dy in range(cy - radius, cy + radius + 1):
        for dx in range(cx - radius, cx + radius + 1):
            m = np.roll(np.roll(V, dy, 1), dx, 2)
            _, edge = shifted(np.zeros((48, 48)), dy, dx)
            v = (base & edge)[None] & (m != LINE)
            cnt = v.sum((1, 2))
            sc = ((m == cg[None]) & v).sum((1, 2)) / np.maximum(cnt, 1)
            better = sc > best
            best[better] = sc[better]; bshift[better] = (dy, dx)
    return best, bshift

def sample_score(cg, valid, samples, center):
    """Compares with acres already confirmed on real captures."""
    best = 0
    cy, cx = center
    for sg, sv in samples:
        for dy in range(cy - 1, cy + 2):
            for dx in range(cx - 1, cx + 2):
                m, v = shifted(sg, dy, dx)
                vv, _ = shifted(sv.astype(int), dy, dx)
                v &= valid & (vv > 0) & (cg != LINE) & (m != LINE)
                if v.sum() > 500:
                    best = max(best, (cg == m)[v].mean())
    return best

def analyse(img, cat, icon_templates, samples=None):
    samples = samples or {}
    img = img.convert('RGB').resize((SIZE, SIZE), Image.Resampling.LANCZOS)
    a = normalize_colors(np.array(img).astype(int))
    cls = classify_capture(img)
    cls[cls == BRIDGE] = WATER

    # buildings
    blds = {}
    for cy, cx, comp, ys, xs in components(building_mask(a), 1500):
        crop = comp[ys.min():ys.max()+1, xs.min():xs.max()+1]
        shape = np.array(Image.fromarray(crop.astype(np.uint8) * 255).resize((32, 32))) > 127
        name = max(icon_templates, key=lambda n: (shape & icon_templates[n]).sum() / max((shape | icon_templates[n]).sum(), 1))
        blds[(int(cy // ACRE), int(cx // ACRE))] = name

    # houses: position (in tiles) inside each acre
    houses = {}
    for cy, cx, comp, ys, xs in components(house_mask(a), 250):
        # bottom of the icon = the house
        hy, hx = ys.max() - (ys.max() - ys.min()) * 0.25, cx
        gy, gx = int(hy // ACRE), int(hx // ACRE)
        ty, tx = int((hy - gy * ACRE) / ACRE * 16), int((hx - gx * ACRE) / ACRE * 16)
        houses.setdefault((gy, gx), []).append((min(ty, 15), min(tx, 15)))

    raw = classify_capture(img)
    grids = capture_grids(img)

    def allowed(n, p):
        e = cat.entries[n]; b = blds.get(p)
        return (e['building'] == b) if b else not e['building']

    def per_entry(best, shifts, allowed_set):
        out = {}
        for k, n in enumerate(cat.owner):
            if n in allowed_set and (n not in out or best[k] > out[n][0]):
                out[n] = (best[k], tuple(shifts[k]))
        return out

    # pass 1: shift of each acre (crop or scale not perfect)
    shifts = {}
    for p, (cg, valid) in grids.items():
        ok = {n for n in range(len(cat.entries)) if allowed(n, p)}
        r1 = per_entry(*batch_score(cg, valid, cat.V), ok)
        top = sorted(r1, key=lambda n: -r1[n][0])[:3]
        idx = np.isin(cat.owner, top)
        b3, s3 = batch_score(cg, valid, cat.V[idx], (0, 0), 3)
        shifts[p] = tuple(s3[b3.argmax()])
    # model: shift = a + b * position (handles a slight zoom)
    G = np.array([[1, p[1], p[0]] for p in shifts])
    dyv = np.array([shifts[p][0] for p in shifts]); dxv = np.array([shifts[p][1] for p in shifts])
    cy = np.linalg.lstsq(G, dyv, rcond=None)[0]; cx = np.linalg.lstsq(G, dxv, rcond=None)[0]

    cells = {}
    for (gy, gx), (cg, valid) in grids.items():
        center = (int(round(cy @ [1, gx, gy])), int(round(cx @ [1, gx, gy])))
        bridge_px = (raw[gy*ACRE:(gy+1)*ACRE, gx*ACRE:(gx+1)*ACRE] == BRIDGE).sum()
        ok = {n for n in range(len(cat.entries)) if allowed(n, (gy, gx))}
        base = per_entry(*batch_score(cg, valid, cat.V, center, 1), ok)
        scores = {}
        near = set(sorted(base, key=lambda n: -base[n][0])[:12])
        for n, (s, _) in base.items():
            e = cat.entries[n]
            if n in near and e['look'] in samples:
                s = max(s, sample_score(cg, valid, samples[e['look']], center) + 0.004)
            for ty, tx in houses.get((gy, gx), []):
                t = e['tiles']; o = e['objs']
                y0, y1, x0, x1 = max(ty-1, 0), min(ty+1, 15), max(tx-1, 0), min(tx+1, 15)
                s -= 0.10 * (t[y0:y1+1, x0:x1+1] == WATER).mean()   # never on water
                s -= 0.03 * o[y0:y1+1, x0:x1+1].mean()              # rarely on trees/rocks
            # bridge visible or not: decisive when the acre exists with AND without a bridge
            pen = 0.03 if e['twin'] else 0.005
            if bridge_px > 60 and not e['bridge']: s -= pen
            if bridge_px <= 60 and e['bridge']: s -= pen
            scores[n] = float(s)
        top = sorted(scores, key=scores.get, reverse=True)[:6]
        fits = {n: score(cg, valid, cat.entries[n]['variants'], True, center, 1)[1:3] for n in top}
        cells[(gy, gx)] = refine(cg, scores, fits)
    analyse.last_shift = (cy, cx)
    return cells, blds, houses, grids

# ---------------------------------------------------------------- whole-map consistency
def edge_cost(e1, side1, e2, side2, sea=False):
    a, b = e1['edges'][side1], e2['edges'][side2]
    if sea: a, b = a[:8], b[:8]   # the sea at the bottom of beaches does not count
    if not a.any() and not b.any(): return 0.0
    da = ndimage.binary_dilation(a); db = ndimage.binary_dilation(b)
    miss = (a & ~db).sum() + (b & ~da).sum()
    return min(miss / 6.0, 1.0)

UNIQUE = ['gate', 'museum', 'nook', 'townhall', 'able', 'lake', 'river_start']

def total(assign, cells, cat, W_EDGE=0.06, W_RULE=0.3):
    E = cat.entries
    s = sum(cells[p][assign[p]] for p in assign)
    for (gy, gx), n in assign.items():
        e = E[n]
        if gx < 4: s -= W_EDGE * edge_cost(e, 'E', E[assign[(gy, gx+1)]], 'W', gy == 4)
        if gy < 4: s -= W_EDGE * edge_cost(e, 'S', E[assign[(gy+1, gx)]], 'N')
        if gy < 4 and gx == 0 and e['edges']['W'].any(): s -= W_EDGE
        if gy < 4 and gx == 4 and e['edges']['E'].any(): s -= W_EDGE
        if e['beach'] != (gy == 4): s -= W_RULE
        if (e['river_start'] or e['building'] == 'gate') and gy != 0: s -= W_RULE
    flags = [E[n] for n in assign.values()]
    for u in UNIQUE:
        cnt = sum(1 for e in flags if e.get(u) or e['building'] == u)
        if cnt > 1: s -= W_RULE * (cnt - 1)
    ramps = sum(e['ramp'] for e in flags)
    s -= W_RULE * 0.5 * abs(ramps - 2)
    return s

def solve(cells, cat, topk=8):
    cand = {p: sorted(sc, key=sc.get, reverse=True)[:topk] for p, sc in cells.items()}
    assign = {p: c[0] for p, c in cand.items()}
    cur = total(assign, cells, cat)
    for _ in range(10):
        changed = False
        for p in cand:
            for n in cand[p]:
                if n == assign[p]: continue
                old = assign[p]; assign[p] = n
                t = total(assign, cells, cat)
                if t > cur + 1e-9: cur, changed = t, True
                else: assign[p] = old
        if not changed: break
    return assign, cand


# ---------------------------------------------------------------- learned references
class SampleStore:
    """Acres confirmed on real captures, reused as references."""
    MAX_PER_LOOK = 8

    def __init__(self, path):
        self.path = path
        self.items = []  # (look, uint8 grid with 255 = masked)
        if os.path.exists(path):
            d = np.load(path, allow_pickle=False)
            looks = [str(x) for x in d['looks']]
            for i, lk in enumerate(looks):
                self.items.append((lk, d[f's{i}']))

    def add(self, look, cg, valid):
        g = cg.astype(np.uint8).copy(); g[~valid] = 255
        self.items.append((look, g))
        same = [i for i, (lk, _) in enumerate(self.items) if lk == look]
        for i in same[:-self.MAX_PER_LOOK]:
            self.items[i] = None
        self.items = [x for x in self.items if x is not None]

    def save(self):
        arrays = {f's{i}': g for i, (lk, g) in enumerate(self.items)}
        np.savez_compressed(self.path, looks=np.array([lk for lk, _ in self.items]), **arrays)

    def as_dict(self):
        d = {}
        for lk, g in self.items:
            d.setdefault(lk, []).append((np.where(g == 255, LIGHT, g).astype(int), g != 255))
        return d


# ---------------------------------------------------------------- application facade
SHEET_CODES = {'Buildings': 'B', 'CliffsRampsGrasslands': 'C', 'Beach': 'E', 'Rivers': 'R', 'River StartLakes': 'L'}

class Analyzer:
    def __init__(self, folder=None):
        self.folder = folder or data_dir()
        self.cat = Catalog(self.folder)
        self.icons = dict(np.load(os.path.join(self.folder, 'icons.npz')))
        self.store = SampleStore(os.path.join(self.folder, 'samples.npz'))
        # house spots already seen for each acre (they are fixed in the game)
        self.slots_path = os.path.join(self.folder, 'house_slots.json')
        self.slots = json.load(open(self.slots_path)) if os.path.exists(self.slots_path) else {}

    def slot_matches(self, entry, houses):
        known = self.slots.get(entry['key'], [])
        return sum(any(abs(ty - y) <= 1 and abs(tx - x) <= 1 for y, x in known) for ty, tx in houses)

    def run(self, img):
        cells, blds, houses, grids = analyse(img, self.cat, self.icons, self.store.as_dict())
        assign, _ = solve(cells, self.cat)
        E = self.cat.entries
        res = {'assign': assign, 'grids': grids, 'cand': {}, 'status': {}, 'scores': cells,
               'houses': houses}
        resolved = set()
        # 48 / 49 / 50: houses always sit on the same spots in a given acre
        for p, n in assign.items():
            if E[n]['blank'] and houses.get(p):
                m = {k: self.slot_matches(E[k], houses[p]) for k in cells[p] if E[k]['blank']}
                best = max(m.values())
                winners = [k for k in m if m[k] == best]
                if best > 0 and len(winners) == 1:
                    assign[p] = winners[0]; resolved.add(p)
        for p, sc in cells.items():
            n = assign[p]
            ranked = sorted(sc, key=sc.get, reverse=True)
            cand = [n] + [m for m in ranked if m != n][:9]
            if E[n]['blank']:
                cand += [m for m in sc if E[m]['blank'] and m not in cand]
            res['cand'][p] = cand
            others = [sc[m] for m in sc if E[m]['look'] != E[n]['look']]
            margin = sc[n] - max(others) if others else 1.0
            status = 'ok' if margin >= 0.03 else ('check' if margin >= 0.01 else 'uncertain')
            if E[n]['blank']:
                status = 'ok' if p in resolved else 'in_game'   # 48/49/50: identical on the map
            res['status'][p] = status
        return res

    def confirm(self, res, assign):
        """Stores the confirmed acres as new references."""
        for p, n in assign.items():
            if res['status'].get(p) == 'in_game' and assign[p] == res['assign'][p]:
                continue  # not confirmed by the player: learn nothing
            cg, valid = res['grids'][p]
            e = self.cat.entries[n]
            self.store.add(e['look'], cg, valid)
            known = self.slots.setdefault(e['key'], [])
            for ty, tx in res['houses'].get(p, []):
                if not any(abs(ty - y) <= 1 and abs(tx - x) <= 1 for y, x in known):
                    known.append([int(ty), int(tx)])
        self.store.save()
        json.dump(self.slots, open(self.slots_path, 'w'))

    def sheet_code(self, assign):
        parts = []
        for gy in range(5):
            for gx in range(5):
                e = self.cat.entries[assign[(gy, gx)]]
                parts.append(f"{SHEET_CODES[e['sheet']]}{e['row']},{e['col']}")
        return 'CFTH1|' + ';'.join(parts)
