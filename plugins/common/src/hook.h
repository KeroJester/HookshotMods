// Inline hooks on the game's host functions (the lifted f_<guest addr> functions and Hookshot's own host code).
// The entry gets `jmp [rip+0]; dq hook` (14 bytes, no register touched); the returned trampoline runs the original:
// the copied whole prologue instructions (RIP-relative operands re-aimed), then a jump back.
//   - jump stubs (jmp rel32 / rel8 / jmp [rip+x] import thunks) are followed to the real function first
//   - a function already hooked this way (another mod, Hookshot's dev overlay) is chained: the new hook takes the
//     entry and its "original" is the previous hook
//   - an instruction the decoder does not know refuses the hook (logged), never guessed
#pragma once
#include "platform.h"

namespace hook {

plat::GuestFn Install(uintptr_t target, plat::GuestFn fn, const char* what);
uintptr_t FollowStubs(uintptr_t target);

}  // namespace hook
