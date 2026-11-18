"""Game folder access for the map tools (no bpy): romfs files (decompressing .zs with the game's dictionaries),
actor packs, and the model index. A game folder is a Hookshot or Longshot folder (it holds romfs/ and mods/), or a
romfs folder itself."""
import os
from functools import lru_cache

from ..vendor import zstd_helper
from ..vendor.byml import parse_byml


class Game:
    def __init__(self, folder: str, overlays=()):
        """overlays: mod folders whose romfs/ files take the place of the game's (the map tools' own mod: maps made
        or edited earlier are read back as they are now). Base reads (byml_base) ignore them."""
        folder = os.path.expanduser(folder)
        self.romfs = folder if os.path.isdir(os.path.join(folder, 'Banc')) else os.path.join(folder, 'romfs')
        if not os.path.isdir(os.path.join(self.romfs, 'Banc')):
            raise FileNotFoundError(f'no romfs (Banc folder) under {folder}')
        self.folder = folder
        self.overlays = [os.path.join(o, 'romfs') for o in overlays if os.path.isdir(os.path.join(o, 'romfs'))]
        self._models = None

    # ---- files
    def path(self, rel: str) -> str:
        for o in self.overlays:
            p = os.path.join(o, rel)
            if os.path.exists(p): return p
        return os.path.join(self.romfs, rel)

    def listdir(self, rel: str):
        names = set()
        for d in [os.path.join(o, rel) for o in self.overlays] + [os.path.join(self.romfs, rel)]:
            if os.path.isdir(d): names.update(os.listdir(d))
        return sorted(names)

    def byml_base(self, rel: str):
        """The game's own file (no overlay)."""
        with open(os.path.join(self.romfs, rel), 'rb') as f:
            data = f.read()
        return parse_byml(zstd_helper.decompress_zs(data, rel, self.romfs) if rel.endswith('.zs') else data)

    def exists(self, rel: str) -> bool:
        return os.path.exists(self.path(rel))

    def read(self, rel: str) -> bytes:
        """A romfs file, decompressed if it is .zs."""
        with open(self.path(rel), 'rb') as f:
            data = f.read()
        return zstd_helper.decompress_zs(data, rel, self.romfs) if rel.endswith('.zs') else data

    def byml(self, rel: str):
        return parse_byml(self.read(rel))

    @lru_cache(maxsize=256)
    def pack(self, rel: str) -> dict:
        """A SARC pack (e.g. Pack/Actor/<name>.pack.zs) as {path: bytes}."""
        return zstd_helper.unpack_sarc_to_dict(self.read(rel))

    # ---- models
    def model_index(self) -> dict:
        """fmdb name -> Model/<project>.<fmdb>.bfres.zs (from the file names)."""
        if self._models is None:
            idx = {}
            for f in os.listdir(self.path('Model')):
                if f.endswith('.bfres.zs') and not f.endswith('.anim.bfres.zs') and f.count('.') >= 3:
                    project, fmdb = f.split('.')[:2]
                    idx.setdefault(fmdb, f'Model/{f}')
            self._models = idx
        return self._models

    @lru_cache(maxsize=4096)
    def actor_model(self, actor: str):
        """An actor name (Gyaml) -> its model file, or None. The actor pack's ModelInfo names
        ModelProjectName + FmdbName; actors without a pack (most Land_*) use their own name as the fmdb."""
        rel = f'Pack/Actor/{actor}.pack.zs'
        if self.exists(rel):
            for k, v in self.pack(rel).items():
                if k.startswith('Component/ModelInfo/') and k.endswith('.bgyml'):
                    info = parse_byml(v)
                    fmdb, project = info.get('FmdbName'), info.get('ModelProjectName')
                    if fmdb and project and self.exists(f'Model/{project}.{fmdb}.bfres.zs'):
                        return f'Model/{project}.{fmdb}.bfres.zs'
                    if fmdb and fmdb in self.model_index():
                        return self.model_index()[fmdb]
        return self.model_index().get(actor)


def is_game_folder(folder):
    """A Longshot / Hookshot folder (romfs/Banc inside) or an extracted romfs (Banc inside)."""
    if not folder: return False
    folder = os.path.expanduser(folder)
    return os.path.isdir(os.path.join(folder, 'romfs', 'Banc')) or os.path.isdir(os.path.join(folder, 'Banc'))


def find_game_folders():
    """Likely game folders on this computer: a Games / Desktop / Documents / Downloads folder (and the home folder),
    up to two levels down (e.g. ~/Games/Longshot/<version>). Folders with a Longshot runtime come first."""
    home = os.path.expanduser('~')
    roots = [home] + [os.path.join(home, n) for n in ('Games', 'Desktop', 'Documents', 'Downloads', 'Applications')]
    for env in ('USERPROFILE', 'PROGRAMFILES', 'PROGRAMFILES(X86)'):
        if os.environ.get(env): roots.append(os.environ[env])
    if os.name == 'nt':
        roots += [f'{d}:\\Games' for d in 'CDEFG']
    found, seen = [], set()
    def visit(d, depth):
        try:
            if is_game_folder(d) and os.path.isdir(os.path.join(d, 'romfs')):
                r = os.path.realpath(d)
                if r not in seen: seen.add(r); found.append(d)
                return
            if depth == 0: return
            for e in sorted(os.scandir(d), key=lambda e: e.name.lower()):
                if e.is_dir(follow_symlinks=False) and not e.name.startswith('.'):
                    visit(e.path, depth - 1)
        except OSError:
            pass
    for r in roots:
        if os.path.isdir(r): visit(r, 1 if r == home else 2)
    def rank(d):
        b = os.path.join(d, 'bin')
        return (0 if any(os.path.exists(os.path.join(b, n)) for n in ('longshot', 'longshot.exe')) else
                1 if any(os.path.exists(os.path.join(b, n)) for n in ('hookshot', 'hookshot.exe')) else 2, d.lower())
    return sorted(found, key=rank)
