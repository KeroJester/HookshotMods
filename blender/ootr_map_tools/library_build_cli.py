# Background entry: blender -b --factory-startup --python library_build_cli.py -- <game folder> [group ...]
# (started by the add-on's Build Asset Library button; also usable from a terminal)
import os
import sys

argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(here))
pkg = os.path.basename(here)
lib = __import__(f'{pkg}.library_build', fromlist=['build'])
game_mod = __import__(f'{pkg}.core.game', fromlist=['Game'])
log_path = os.path.join(lib.library_dir(), 'build.log')
os.makedirs(lib.library_dir(), exist_ok=True)
with open(log_path, 'w') as logf:
    def log(m):
        print(m); logf.write(m + '\n'); logf.flush()
    lib.build(game_mod.Game(argv[0]), set(argv[1:]) or None, log)
    log('done')
