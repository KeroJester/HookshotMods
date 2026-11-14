# Shared code for the code mods

Sources every code mod (plugins/<mod>/build.sh) compiles in:

- `platform.*`: the game's symbols (Linux ELF .symtab, Windows linker map), guest memory, the log, memory near code.
- `hook.*`: inline hooks on the game's host functions (chained when another hook is already there).
- `gamefiles.*`: the player's own game files (RomFS path of the file the game would load, zstd with the game's
  dictionaries, SARC listing).
- `vfs.*`: changed game files without files on disk (the game reads a patched copy made in memory).
