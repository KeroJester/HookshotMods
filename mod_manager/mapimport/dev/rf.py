# rf.py: read a romfs file decompressed; python3 rf.py <relpath> [out]
import sys, os
sys.path.insert(0, os.path.expanduser('~/Projects/hookshot-mods'))
from mod_manager.zstd_helper import decompress_zs
ROMFS = os.path.expanduser('~/Games/Hookshot/romfs')
def rf(rel):
    d = open(os.path.join(ROMFS, rel), 'rb').read()
    return decompress_zs(d, rel, ROMFS) if rel.endswith('.zs') else d
if __name__ == '__main__':
    d = rf(sys.argv[1])
    if len(sys.argv) > 2: open(sys.argv[2], 'wb').write(d)
    else: sys.stdout.buffer.write(d)
