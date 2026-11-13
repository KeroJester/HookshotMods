# suyu ASTC decoder (vendored)

The ASTC texture decoder from the suyu emulator project, used by Item Controls to decode the game's item icons
(UI/Tex/Icon) on the player's machine. Copied unchanged from Hookshot's copy of suyu (gpu/third_party/suyu, Hookshot
commit c6c72a92466f611a78e322673076ca2047ef238d), as Hookshot's repository is no longer available.

Files keep their own copyright and SPDX headers: suyu's code is GPL-3.0-or-later (see GPL-3.0-or-later.txt); the
decoder's core derives from the University of North Carolina at Chapel Hill's ASTC decoder (Apache-2.0, as noted in
astc.cpp). `boost/container/static_vector.hpp` comes from plugins/item_controls/shim.
