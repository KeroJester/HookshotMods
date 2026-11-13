#!/usr/bin/env python3
"""gdis.py <guest addr hex> [count]: disassemble the guest function containing an address (or `count` instructions
from it), with function boundaries from Hookshot's lifted f_<addr> symbols. Needs re/img.elf in the game folder and
a function-start list (nm bin/hookshot | f_ names) in $FSTARTS."""
import bisect, os, subprocess, sys
GAME = os.environ.get("HOOKSHOT_GAME_DIR", "/home/justin/Games/Hookshot")
starts = [int(l, 16) for l in open(os.environ["FSTARTS"])]
a = int(sys.argv[1], 16)
i = bisect.bisect_right(starts, a) - 1
lo = starts[i] if len(sys.argv) < 3 else a
hi = starts[i + 1] if len(sys.argv) < 3 else a + 4 * int(sys.argv[2])
print(f"; f_{starts[i]:x} .. {starts[i+1]:x} ({(starts[i+1]-starts[i])//4} instructions)")
out = subprocess.run(["llvm-objdump", "-D", "--no-show-raw-insn", "--triple=aarch64", f"--start-address={lo:#x}",
                      f"--stop-address={hi:#x}", os.path.join(GAME, "re", "img.elf")], capture_output=True, text=True).stdout
print("\n".join(l for l in out.splitlines() if l.strip().startswith(("8", "  8")) or ":\t" in l))
