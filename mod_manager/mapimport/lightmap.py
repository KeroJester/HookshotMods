"""Lightmap UVs for the scene bake (RESEARCH_NOTES.md §80s): planar charts per material, packed into one atlas.

The game binds a model's `bake` UV (per material, 0..1) into a bake atlas with a per-material offset and scale
(Bake/Scene/*.bkres: TexcoordOffset / TexcoordScale). unwrap_materials() gives every material its own chart sheet:
triangles grouped into charts of edge-connected, nearly coplanar faces, each chart projected on its plane at
`density` texels per metre and shelf-packed with a texel of padding; the sheets are then packed into the atlas.
Vertices on chart borders are split (each chart has its own UVs).
"""
import numpy as np


def _charts(P, T, cos_limit=0.94):
    """-> chart id per triangle (edge-connected faces whose normal stays within cos_limit of the chart's first)."""
    fn = np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]])
    fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
    key = {tuple(np.round(p, 3)): k for k, p in enumerate(P)}
    vid = np.array([key[tuple(np.round(p, 3))] for p in P])         # (positions shared across split vertices)
    edges = {}
    for t, tri in enumerate(T):
        for a, b in ((0, 1), (1, 2), (2, 0)):
            e = tuple(sorted((vid[tri[a]], vid[tri[b]])))
            edges.setdefault(e, []).append(t)
    nbr = [[] for _ in T]
    for ts in edges.values():
        for i in ts:
            for j in ts:
                if i != j: nbr[i].append(j)
    chart = -np.ones(len(T), int)
    c = 0
    for s in range(len(T)):
        if chart[s] >= 0: continue
        chart[s] = c
        stack = [s]
        while stack:
            t = stack.pop()
            for u in nbr[t]:
                if chart[u] < 0 and fn[u] @ fn[s] > cos_limit:
                    chart[u] = c
                    stack.append(u)
        c += 1
    return chart, fn


def _shelf(sizes, width):
    """sizes [(w, h)] in texels -> positions [(x, y)] and the used height (tallest first)."""
    order = sorted(range(len(sizes)), key=lambda i: -sizes[i][1])
    pos = [None] * len(sizes)
    x = y = row = 0
    for i in order:
        w, h = sizes[i]
        if x + w > width:
            x, y, row = 0, y + row, 0
        pos[i] = (x, y)
        x += w
        row = max(row, h)
    return pos, y + row


def unwrap(mesh_list, density=2.0, pad=1):
    """Meshes of one material (dicts with pos, nrm, tan, uv, col, idx) -> (new meshes with 'bake' in 0..1 of the
    sheet, sheet size (w, h) in texels)."""
    charts_all, sizes = [], []
    for mi, m in enumerate(mesh_list):
        P, T = m['pos'], m['idx'].reshape(-1, 3).astype(int)
        chart, fn = _charts(P, T)
        for c in range(chart.max() + 1):
            ts = np.nonzero(chart == c)[0]
            n = fn[ts].mean(0); n /= np.linalg.norm(n)
            a = np.array([1.0, 0, 0]) if abs(n[0]) < 0.9 else np.array([0, 1.0, 0])
            u = np.cross(n, a); u /= np.linalg.norm(u); v = np.cross(n, u)
            vs = np.unique(T[ts])
            q = np.stack([P[vs] @ u, P[vs] @ v], 1) * density
            lo = q.min(0)
            w, h = (np.ceil(q.max(0) - lo) + 1 + 2 * pad).astype(int)
            charts_all.append(dict(mesh=mi, tris=ts, verts=vs, q=q - lo + pad + 0.5))
            sizes.append((int(w), int(h)))
    total = sum(w * h for w, h in sizes)
    width = max(max(w for w, _ in sizes), int(np.ceil(np.sqrt(total * 1.15))))
    pos, height = _shelf(sizes, width)
    out = []
    for mi, m in enumerate(mesh_list):
        T = m['idx'].reshape(-1, 3).astype(int)
        newv = {}                                   # (chart, old vertex) -> new vertex
        cols = {k: [] for k in ('pos', 'nrm', 'tan', 'uv', 'col', 'bake')}
        idx = np.zeros_like(T)
        for ci, ch in enumerate(charts_all):
            if ch['mesh'] != mi: continue
            x0, y0 = pos[ci]
            local = {v: ch['q'][k] for k, v in enumerate(ch['verts'])}
            for t in ch['tris']:
                for j in range(3):
                    v = T[t, j]
                    k = (ci, v)
                    if k not in newv:
                        newv[k] = len(cols['pos'])
                        for name in ('pos', 'nrm', 'tan', 'uv'): cols[name].append(m[name][v])
                        cols['col'].append(m['col'][v] if 'col' in m else np.ones(4))
                        cols['bake'].append(((local[v][0] + x0) / width, (local[v][1] + y0) / height))
                    idx[t, j] = newv[k]
        nm = dict(m)
        for name in cols: nm[name] = np.array(cols[name], float)
        nm['idx'] = idx.reshape(-1).astype(np.uint16)
        nm['tris'] = idx
        assert len(nm['pos']) < 65536
        out.append(nm)
    return out, (width, height)


def pack_sheets(sheets, pad=2):
    """sheet sizes [(w, h)] -> (atlas size (W, H), offsets [(x, y)] in texels)."""
    sizes = [(w + pad, h + pad) for w, h in sheets]
    total = sum(w * h for w, h in sizes)
    width = max(max(w for w, _ in sizes), int(np.ceil(np.sqrt(total * 1.2) / 4) * 4))
    pos, height = _shelf(sizes, width)
    return (width, height), pos


def rasterize(P, N, UV, T, size):
    """Triangles with atlas UVs (texel units) -> dict texel (x, y) -> (position, normal) at the texel centre (the
    triangle covering it; texels on edges take the nearest triangle's clamped point)."""
    W, H = size
    out = {}
    for tri in T:
        a, b, c = UV[tri]
        x0, y0 = np.floor(np.minimum(np.minimum(a, b), c)).astype(int) - 1
        x1, y1 = np.ceil(np.maximum(np.maximum(a, b), c)).astype(int) + 1
        xs, ys = np.meshgrid(np.arange(max(x0, 0), min(x1, W)), np.arange(max(y0, 0), min(y1, H)))
        pts = np.stack([xs.ravel() + 0.5, ys.ravel() + 0.5], 1)
        m = np.array([[b[0] - a[0], c[0] - a[0]], [b[1] - a[1], c[1] - a[1]]])
        if abs(np.linalg.det(m)) < 1e-12: continue
        lam = np.linalg.solve(m, (pts - a).T).T
        w = np.c_[1 - lam.sum(1), lam]
        inside = (w >= -1e-6).all(1)
        near = (w >= -0.75).all(1)                  # (padding: texels just outside take the clamped point)
        wc = np.clip(w, 0, None); wc /= wc.sum(1, keepdims=True)
        p = wc @ P[tri]; n = wc @ N[tri]
        for k in np.nonzero(near)[0]:
            key = (int(pts[k, 0]), int(pts[k, 1]))
            if inside[k] or key not in out:
                out[key] = (p[k], n[k] / max(np.linalg.norm(n[k]), 1e-9), bool(inside[k]))
    return out
