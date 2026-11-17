"""Game folder access for the map tools (no bpy): romfs files (decompressing .zs with the game's dictionaries),
actor packs, and the model index. A game folder is a Hookshot or Longshot folder (it holds romfs/ and mods/), or a
romfs folder itself."""
import os
from functools import lru_cache

from ..vendor import zstd_helper
from ..vendor.byml import parse_byml


class Game:
    def __init__(self, folder: str):
        folder = os.path.expanduser(folder)
        self.romfs = folder if os.path.isdir(os.path.join(folder, 'Banc')) else os.path.join(folder, 'romfs')
        if not os.path.isdir(os.path.join(self.romfs, 'Banc')):
            raise FileNotFoundError(f'no romfs (Banc folder) under {folder}')
        self.folder = folder
        self._models = None

    # ---- files
    def path(self, rel: str) -> str:
        return os.path.join(self.romfs, rel)

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
