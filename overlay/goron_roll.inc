// -------------------------------------------------------------
// Goron Link roll: a port of Majora's Mask Player_Action_96 (zeldaret/mm z_player.c), grounded path.
// -------------------------------------------------------------
// MM runs it at 20 fps in MM units (1 unit ~ 0.0236 m at Goron Link's scale here, RESEARCH_NOTES §27) and binary
// angles (0x10000 = 360 deg). The port keeps MM's state and math verbatim and ticks it at a fixed 20 Hz from real time,
// then drives the game's character controller with the result: forward speed (cct->[0x28] + 0x1d8, m/s) and move
// direction (+0x1c0), written right after the engine's own movement step f_809552f0 (see goron_hooks.inc).
//
// Mapping of MM fields: unk_B08 = rollSpeed, unk_B0C = bounce, av1 = phase, av2 = spin, unk_B8C = steerLock,
// unk_B8E = noInput, actor.home.rot.y = homeYaw, currentYaw, actor.shape.rot.y = shapeYaw, shape.rot.x = spinAngle.
// unk_B86[1] = spikes, PLAYER_STATE3_80000 = spikeFlag (spiked roll, magic draining).
// Not ported yet: B jump + ground pound, wall bonk / reflection, slope terms (floorPitch / slope normal are 0 here),
// ice / sand acceleration, sounds and dust.

#include <cmath>

namespace goron_roll {

constexpr float kUnitMeters = 0.0236f;          // MM unit -> metres
constexpr float kMmFps = 20.0f;
constexpr float kFramerateDivisorHalf = 1.5f;   // gFramerateDivisorHalf (R_UPDATE_RATE 3 * 0.5) in normal gameplay
// z_actor.c Actor_UpdatePos: pos += velocity * sActorMovementScale, set by Actor_SetMovementScale(framerateDivisor)
// = 3 * 0.5 = 1.5 at 20 fps. So a speed of v units moves v * 1.5 units per 20 Hz frame.
constexpr float kActorMovementScale = 1.5f;
// MM speed -> m/s, calibrated on Link himself: MM's human Link runs at R_RUN_SPEED_LIMIT 550 -> 5.5, this game's
// child Link runs at 4.5 m/s (measured: CCT speed 4.500, RESEARCH_NOTES §44). So 1 MM speed unit = 4.5 / 5.5 m/s.
// (Cross-check: MM roll 6.583 / run 5.5 = 1.20; this game's dodge roll 5.625 / run 4.5 = 1.25.)
// The model-size estimate kUnitMeters * kMmFps * kActorMovementScale gave 0.708 and is no longer used.
constexpr float kLinkRunMps = 4.5f;
constexpr float kMmLinkRunSpeed = 5.5f;
constexpr float kSpeedToMps = kLinkRunMps / kMmLinkRunSpeed;

// ---- MM z_lib.c helpers (verbatim semantics) ----------------------------------------------------------------
static inline int16_t Trunc16(float f) { return (int16_t)(int32_t)f; }
static bool StepToF(float* v, float target, float step) {
    if (step != 0.0f) {
        if (target < *v) step = -step;
        *v += step;
        if ((*v - target) * step >= 0) { *v = target; return true; }
    } else if (target == *v) return true;
    return false;
}
static bool AsymStepToF(float* v, float target, float incr, float decr) {
    float step = (target >= *v) ? incr : decr;
    if (step != 0) {
        if (target < *v) step = -step;
        *v += step;
        if ((*v - target) * step >= 0) { *v = target; return true; }
    } else if (target == *v) return true;
    return false;
}
static bool StepUntilF(float* v, float limit, float step) {   // Math_StepUntilF
    *v += step;
    if ((step > 0 && *v >= limit) || (step < 0 && *v <= limit)) { *v = limit; return true; }
    return false;
}
static bool ScaledStepToS(int16_t* v, int16_t target, int16_t step) {
    if (step != 0) {
        if ((int16_t)(*v - target) > 0) step = -step;
        *v += Trunc16(step * kFramerateDivisorHalf);
        if ((int16_t)(*v - target) * step >= 0) { *v = target; return true; }
    } else if (target == *v) return true;
    return false;
}
static bool AsymStepToS(int16_t* v, int16_t target, int16_t incr, int16_t decr) {
    int16_t step = ((target - *v) >= 0) ? incr : decr;
    if (step != 0) {
        if (target < *v) step = -step;
        *v += step;
        if ((*v - target) * step >= 0) { *v = target; return true; }
    } else if (target == *v) return true;
    return false;
}
static inline float SinS(int16_t a) { return sinf(a * (float)M_PI / 32768.0f); }
static inline float CosS(int16_t a) { return cosf(a * (float)M_PI / 32768.0f); }
static inline int16_t Atan2S_XY(float z, float x) {        // Math_Atan2S_XY(z, x): yaw whose sin is x and cos is z
    return (int16_t)(int32_t)lrintf(atan2f(x, z) * 32768.0f / (float)M_PI);
}
static inline int Abs(int v) { return v < 0 ? -v : v; }
static bool StepToS(int16_t* v, int16_t target, int16_t step) {     // Math_StepToS
    if (step != 0) {
        if (target < *v) step = -step;
        *v += step;
        if ((*v - target) * step >= 0) { *v = target; return true; }
    } else if (target == *v) return true;
    return false;
}
static bool StepToC(int* v, int target, int step) {                 // Math_StepToC (s8)
    if (step != 0) {
        if (target < *v) step = -step;
        *v += step;
        if ((*v - target) * step >= 0) { *v = target; return true; }
    } else if (target == *v) return true;
    return false;
}

// ---- MM z_parameter.c magic, the parts the spiked roll uses (MAGIC_CONSUME_GORON_ZORA) --------------------------
// The meter itself is the game's (read/written through get/set, in MM magic units); MM's state machine is ours.
enum MagicState { MAGIC_STATE_IDLE = 0, MAGIC_STATE_CONSUME_GORON_ZORA_SETUP = 1, MAGIC_STATE_CONSUME_GORON_ZORA = 2 };
struct Magic {
    int state = MAGIC_STATE_IDLE;
    int timer = 0;                                  // interfaceCtx->magicConsumptionTimer
    int (*get)() = nullptr;                         // gSaveContext.save.saveInfo.playerData.magic
    void (*set)(int) = nullptr;
    int value() const { return get ? get() : 0; }
    // Magic_Consume(play, 2, MAGIC_CONSUME_GORON_ZORA)
    bool ConsumeGoronZora() {
        if (value() != 0) { timer = 10; state = MAGIC_STATE_CONSUME_GORON_ZORA_SETUP; return true; }
        return false;
    }
    void Reset() { state = MAGIC_STATE_IDLE; }      // Magic_Reset
    // Magic_Update, once per game frame (the GORON_ZORA states; pause / message / cutscene gates are the game's)
    void Update() {
        switch (state) {
        case MAGIC_STATE_CONSUME_GORON_ZORA_SETUP: {
            int m = value() - 2;
            if (m <= 0) m = 0;
            if (set) set(m);
            state = MAGIC_STATE_CONSUME_GORON_ZORA;
        }   [[fallthrough]];
        case MAGIC_STATE_CONSUME_GORON_ZORA:
            if (--timer == 0) {
                int m = value() - 1;
                if (m <= 0) m = 0;
                if (set) set(m);
                timer = 10;
            }
            break;
        default: break;
        }
    }
};

// Player_SetCylinderForAttack while rolling (end of Player_Action_96): which MM attack is active
enum Attack { ATK_NONE = 0, ATK_NORMAL_ROLL = 1, ATK_GORON_SPIKES = 2, ATK_GORON_POUND = 3 };

struct State {
    bool active = false;
    int curlTicks = 0;              // landing_roll at 1.5x, frames 0..6 (func_80836B3C): Action_96 returns early meanwhile
    int phase = 0;                  // av1
    int16_t spin = 0;               // av2 (binang per frame)
    float rollSpeed = 0;            // unk_B08
    float bounce = 0;               // unk_B0C
    float linearVelocity = 0;
    int16_t homeYaw = 0, currentYaw = 0, shapeYaw = 0, spinAngle = 0;
    int steerLock = 0, noInput = 0; // unk_B8C, unk_B8E
    float abc = 0, b48 = 0;         // unk_ABC (squash/stretch), unk_B48 (its spring velocity): func_808577E0
    int16_t leanZ = 0;              // actor.shape.rot.z
    int16_t wobble = 0, wobbleYaw = 0;   // unk_B86[0], unk_B28
    // air / ground pound (func_80857640, func_80857AEC, the air branch of Player_Action_96)
    bool airborne = false;          // !(bgCheckFlags & BGCHECKFLAG_GROUND)
    bool landed = false;            // BGCHECKFLAG_GROUND_TOUCH on the next tick
    float velY = 0;                 // actor.velocity.y (MM units / frame)
    float posY = 0;                 // height above the take-off point (MM units)
    float gravity = 0;              // actor.gravity
    int b8a = 0;                    // unk_B8A: pound recovery frames
    float b10 = 0;                  // unk_B10[0]: pound glow (ball env colour lerp)
    int attack = ATK_NONE;          // Player_SetCylinderForAttack this tick
    float attackRadius = 0;         // its cylinder radius (MM units)
    int quake = 0;                  // quake requested this tick: 1 = bonk (33267, 3, 12)
    int16_t spikes = 0;             // unk_B86[1]: 0 = no spikes, 1..7 growing (drawn at 0.5, 1.0, then full)
    bool spikeFlag = false;         // PLAYER_STATE3_80000: spiked roll (magic draining)
    int sfx = 0;                    // sound events this tick (SFX_* bits) for the host
    float rollSfxParam = 0;         // Audio_PlaySfx_AtPosWithSyncedFreqAndVolume parameter (unk_B08)
    float wobbleSfxParam = 0;       // (sp54)
    float prevVelocity = 0;         // actor.velocity along currentYaw, from the previous frame (func_808576BC)
    int16_t prevYaw = 0;
    float curlFrame = 0;            // pg_maru_change frame while curling (standing entry)
    int16_t wallYawIn = 0;
    bool standingEntry = false;
};

enum { SFX_CHARGE = 1, SFX_CHARGE_DASH = 2, SFX_CHARGE_FAILED = 4, SFX_ROLL = 8, SFX_WOBBLE = 16, SFX_SLIP = 32,
       SFX_DUST = 64, SFX_BALLJUMP = 128, SFX_POUND = 256, SFX_LAND = 512, SFX_BONK = 1024, SFX_REFLECT = 2048 };
constexpr float kGravity = -1.4f;           // func_8083BF54: REG(68) / 100, D_801BFE14[PLAYER_BOOTS_GORON] = -140
constexpr float kTerminalVelocity = -20.0f;

struct Input {
    bool aHeld;                     // CHECK_BTN_ALL(cur.button, BTN_A)
    bool attackPressed = false;     // CHECK_BTN_ALL(press.button, BTN_B): MM's attack button (Y on this game's pad)
    bool wallTouched = false;       // BGCHECKFLAG_WALL (+ PLAYER_WALL_INTERACT), detected by the host
    int16_t wallYaw = 0;            // actor.wallYaw: yaw of the wall's normal (pointing away from the wall)
    float stickMag;                 // 0..60, MM sPlayerControlStickMagnitude
    int16_t stickYaw;               // world yaw of the stick (stick angle + camera input yaw)
};

// Player_CalcSpeedAndYawFromControlStick, SPEED_MODE_LINEAR, then the roll's *2.6 (Player_Action_96)
static void SpeedAndYaw(const State& s, const Input& in, float* speedTarget, int16_t* yawTarget) {
    if (in.stickMag <= 0.0f) { *speedTarget = 0; *yawTarget = s.shapeYaw; return; }
    float speedCap = 6.0f;                                    // unk_B50 = R_RUN_SPEED_LIMIT / 100
    float t = in.stickMag * 0.8f;                             // linear mode
    t = t * 0.14f - 8.0f * 0.0f;                              // (floor pitch influence: flat)
    *speedTarget = t < 0 ? 0 : (t > speedCap ? speedCap : t);
    *yawTarget = in.stickYaw;
}

// func_808577E0: unk_ABC follows the spin through the unk_B48 spring
static void Squash(State& s) {
    float want = Abs(s.spin) * 0.00004f;
    if (s.abc < want) s.b48 += 0.08f; else s.b48 += -0.07f;
    s.b48 = s.b48 < -0.2f ? -0.2f : (s.b48 > 0.14f ? 0.14f : s.b48);
    if (fabsf(s.b48) < 0.12f) {
        if (StepUntilF(&s.abc, want, s.b48)) s.b48 = 0.0f;
    } else {
        s.abc += s.b48;
        s.abc = s.abc < -0.7f ? -0.7f : (s.abc > 0.3f ? 0.3f : s.abc);
    }
}

// func_80839F98 (stationary): func_80836AD8 + pg_maru_change frames 0..7 at PLAYER_ANIM_ADJUSTED_SPEED
static void EnterStanding(State& s, int16_t facingYaw) {
    s = State();
    s.active = true;
    s.standingEntry = true;
    s.curlFrame = 0.0f;
    s.currentYaw = s.homeYaw = s.shapeYaw = facingYaw;
}

// func_80836AD8 + func_80836B3C (Goron): enter the roll while moving
static void Enter(State& s, float entrySpeedUnits, int16_t facingYaw) {
    s = State();
    s.active = true;
    s.curlTicks = 4;                                          // 6 frames at 1.5x
    s.linearVelocity = entrySpeedUnits;
    s.prevVelocity = entrySpeedUnits;
    s.prevYaw = facingYaw;
    s.currentYaw = s.homeYaw = s.shapeYaw = facingYaw;
}

// func_80857950's condition: uncurl (A released with no spikes)
static bool WantsUncurl(const State& s, const Input& in) {
    return (s.spikes == 0 && !in.aHeld) || (s.phase == 3 && s.velY < 0.0f);
}

// One 20 Hz tick of Player_Action_96 (grounded).
static void Tick(State& s, const Input& in, Magic& magic) {
    s.sfx = 0;
    const float velPrev = s.prevVelocity;                     // (actor.velocity: last frame's speed and direction)
    const int16_t yawPrev = s.prevYaw;
    if (!s.active) return;
    if (s.standingEntry && s.phase == 0) {                    // pg_maru_change 0 -> 7 at 2/3 frame per tick
        s.curlFrame += 2.0f / 3.0f;
        if (s.curlFrame >= 7.0f) {                            // func_80857A44 (animation finished)
            s.curlFrame = 7.0f;
            s.phase = 4;
            s.spin = Trunc16(s.linearVelocity * 500.0f);
            s.rollSpeed = s.linearVelocity;
            s.bounce = 0;
            s.homeYaw = s.currentYaw;
        }
        return;
    }
    if (s.curlTicks > 0) {                                    // (av1 == 0) && !func_80857A44: animation still running
        if (--s.curlTicks == 0) {                             // func_80857A44
            s.phase = 4;
            s.spin = Trunc16(s.linearVelocity * 500.0f);
            s.rollSpeed = s.linearVelocity;
            s.bounce = 0;
            s.homeYaw = s.currentYaw;
        }
        return;
    }
    s.gravity = kGravity;                                     // func_8083BF54 (each update, before the action)
    Squash(s);                                                // Player_Action_96: func_808577E0 every tick
    s.quake = 0;
    // func_80840A30(play, this, &unk_B08, 12.0f): head-on wall at speed >= 12 -> bonk (the rolling branch)
    if (in.wallTouched && s.rollSpeed >= 12.0f && Abs((int16_t)(s.currentYaw - (int16_t)(s.wallYawIn = in.wallYaw) - 0x8000)) < 0x1C00) {
        s.linearVelocity = -s.linearVelocity;
        s.quake = 1;                                          // Player_RequestQuake(33267, 3, 12) + rumble + impact
        s.sfx |= SFX_BONK;                                    // NA_SE_PL_BODY_HIT
        s.linearVelocity *= 0.1f;                             // (back in Player_Action_96)
        s.velY = 10.0f; s.airborne = true;                    // func_80834CD0(this, 10.0f, 0)
        if (s.spikes != 0) { s.spikes = 0; s.phase = 3; }
    } else if (in.wallTouched && s.rollSpeed >= 12.0f) {      // glancing: reflect off the wall
        int16_t d = (int16_t)(s.currentYaw - (int16_t)(in.wallYaw + 0x8000));
        int ad = Abs(d);
        s.bounce += s.rollSpeed * 0.05f;
        int16_t snapped = (int16_t)(((d >= 0) ? 1 : -1) * ((ad + 0x100) & ~0x1FF));
        s.currentYaw += (int16_t)(0x8000 - (int16_t)(snapped * 2));
        s.homeYaw = s.currentYaw;
        s.shapeYaw = s.currentYaw;
        s.steerLock = 4;
        s.sfx |= SFX_REFLECT;                                 // NA_SE_IT_GORON_ROLLING_REFLECTION
    }
    float speedTarget = 0;
    int16_t yawTarget = s.currentYaw;
    if (s.noInput != 0) s.noInput--;
    else { SpeedAndYaw(s, in, &speedTarget, &yawTarget); speedTarget *= 2.6f; }
    if (s.steerLock != 0) { s.steerLock--; yawTarget = s.currentYaw; }

    if (s.spikes != 0) {
        speedTarget = 18.0f;
        StepToC(&s.phase, 4, 1);
        if (s.spikeFlag && (!in.aHeld || magic.value() == 0 || (s.phase == 4 && s.rollSpeed < 12.0f))) {
            if (StepToS(&s.spikes, 0, 1)) {
                s.spikeFlag = false;
                magic.Reset();
                s.sfx |= SFX_CHARGE_FAILED;                   // NA_SE_PL_GORON_BALL_CHARGE_FAILED
            }
            s.phase = 4;
        } else if (s.spikes < 7) {
            s.spikes++;                                       // (unk_3D0.unk_00 = 2: the spike pop effect)
        }
    }

    int32_t spDC = (int32_t)(speedTarget * 900.0f);
    int16_t sp90 = s.currentYaw;
    int16_t sp8E = s.currentYaw - s.homeYaw;
    float sp88 = CosS(sp8E);
    // unk_B10[0]: the pound glow follows unk_B8A
    AsymStepToF(&s.b10, (s.b8a != 0) ? 1.0f : 0.0f, 0.8f, 0.05f);
    bool ground = !s.airborne, jumped = false, normalGround = false;
    if (ground) {
        if (s.landed) {                                       // func_80857AEC (BGCHECKFLAG_GROUND_TOUCH)
            s.landed = false;
            s.bounce += s.rollSpeed * 0.05f;
            if (s.spikes == 0) {
                if (s.phase == 1) {                           // the pound lands
                    s.phase = 2;
                    s.sfx |= SFX_POUND;                       // Player_RequestQuakeAndRumble(NA_SE_PL_GORON_PUNCH)
                    s.spin = 0;                               // (+ white shockwave, EN_TEST)
                    s.rollSpeed = 0.0f;
                } else {
                    s.phase = 4;
                }
            }
            s.sfx |= SFX_LAND;                                // Player_AnimSfx_PlayFloorLand
        }
        if (s.phase == 2) {                                   // pound recovery
            if (s.b8a == 0) s.phase = 4;
            else { s.b8a--; s.abc = 0.0f; s.b48 = 0.14f; }
        } else if (s.spikes == 0 && in.attackPressed) {       // func_80857640(this, 14.0f, 0x1F40)
            s.velY = 14.0f;                                   // func_80834CD0 (clears the ground flag)
            s.airborne = true;
            s.linearVelocity = 0.0f;                          // Player_StopHorizontalMovement
            if (s.spin < 0x1F40) s.spin = 0x1F40;
            s.phase = 1;
            s.b48 = 1.0f;
            s.sfx |= SFX_BALLJUMP;
            jumped = true;
        } else {
            normalGround = true;
        }
    }
    if (normalGround) {
        if (s.spikes == 0) {
            s.bounce = 0;
            if (s.phase >= 0x36) {                                // charged: spikes out, full speed
                magic.ConsumeGoronZora();
                s.rollSpeed = 18.0f;
                s.spikes = 1;
                s.spikeFlag = true;
                s.sfx |= SFX_CHARGE_DASH;                         // NA_SE_PL_GORON_BALL_CHARGE_DASH
            }
        } else {
            s.bounce = s.bounce < 0.0f ? 0.0f : (s.bounce > 0.9f ? 0.9f : s.bounce);
        }
        float spBC = (1.0f - s.bounce) * s.rollSpeed * sp88;
        if (spBC < 0.0f || (speedTarget == 0.0f && Abs(sp8E) > 0xFA0)) spBC = 0;
        StepToF(&s.bounce, 0.0f, fabsf(sp88) * 20.0f);
        int32_t var_a0 = (int32_t)(spBC * 500.0f);
        if (var_a0 < 0) var_a0 = 0;
        int32_t spC0 = (int32_t)(speedTarget * 400.0f) - var_a0;
        if (spC0 < 0) spC0 = 0;
        if (spDC < var_a0) spDC = var_a0;
        float spAC = spBC * SinS(s.homeYaw), spA8 = spBC * CosS(s.homeYaw);
        float spB4 = s.rollSpeed * SinS(s.currentYaw), spB0 = s.rollSpeed * CosS(s.currentYaw);
        float spA4 = spB4 - spAC, spA0 = spB0 - spA8;
        s.linearVelocity = spBC;
        s.currentYaw = s.homeYaw;
        float spCC = speedTarget;
        int16_t spCA = yawTarget;

        // func_8083A4A4(this, &spCC, &spCA, (av1 >= 5) ? 0.0f : 1.0f)
        bool braking = false;
        {
            int16_t d = s.currentYaw - spCA;
            if (Abs(d) > 0x6000) {
                if (StepToF(&s.linearVelocity, 0.0f, s.phase >= 5 ? 0.0f : 1.0f)) { spCC = 0; spCA = s.currentYaw; }
                else braking = true;
            }
        }
        if (braking) {
            if (s.spikes == 0) s.phase = 4;
            if (s.phase == 4) spDC = -0xFA0;
        } else {
            float sp84 = s.spin * 0.0003f;                        // (ice/sand: 0.08 when spC0 >= 0x7D0)
            float sp80 = SinS(0) * 8.0f + 0.6f;                   // (floor pitch: flat)
            if (s.spikes == 0) {
                if (magic.state == MAGIC_STATE_IDLE && magic.value() >= 2 && s.spin >= 0x36B0) {
                    s.phase++;                                    // charging (NA_SE_PL_GORON_BALL_CHARGE)
                    s.sfx |= SFX_CHARGE;
                } else {
                    s.phase = 4;
                }
            }
            if (speedTarget != spCC) s.currentYaw = yawTarget;
            if (sp84 < 0) sp84 = 0;
            if (sp80 < 0) sp80 = 0;
            AsymStepToF(&s.linearVelocity, speedTarget, sp84, sp80);
            int32_t spC8i = (int32_t)(fabsf(s.linearVelocity) * 20.0f) + 300;   // actor.speed ~ linearVelocity
            if (spC8i < 100) spC8i = 100;
            int16_t spC8 = (int16_t)spC8i;
            int16_t sp7C = (int16_t)(int32_t)((int16_t)(yawTarget - s.currentYaw) * -0.5f);
            s.bounce += (float)(sp7C * sp7C) * 8e-9f;
            ScaledStepToS(&s.currentYaw, yawTarget, spC8);
            ScaledStepToS(&s.leanZ, (int16_t)(0 + sp7C), spC8);   // (slope probe term var_a3 = 0 on flat ground)
        }
        spBC = s.linearVelocity;
        s.homeYaw = s.currentYaw;
        s.currentYaw = sp90;
        float spB8 = sqrtf(spA4 * spA4 + spA0 * spA0);
        if (s.spikes != 0) {
            if (Abs(sp8E) + Abs(0 /* floorPitch */) > 0x3A98) {   // turned too sharply: spikes lost, no input for 20 ticks
                s.spikes = 0;
                s.phase = 4;
                s.noInput = 0x14;
                s.spin = 0;
                s.spikeFlag = false;
                magic.Reset();
            }
        }   // (else: slope normal 0 here, so the slope-push branch keeps spA4/spA0)
        if (spB8 != 0.0f) {
            float sp54 = spB8 - 0.3f;
            if (sp54 < 0) sp54 = 0;
            float k = sp54 / spB8;
            spA4 *= k; spA0 *= k;
            if (sp54 != 0.0f) s.wobbleYaw = Atan2S_XY(spA0, spA4);
            if (s.spin == 0) {
                int32_t old = s.wobble, add = (int32_t)(sp54 * 800.0f);
                s.wobble += (int16_t)add;
                if (add != 0 && (int64_t)(old + add) * old <= 0) {   // grounded: NA_SE_PL_GORON_ROLL synced to sp54
                    s.sfx |= SFX_WOBBLE;
                    s.wobbleSfxParam = sp54;
                }
            }
        }
        spAC = SinS(s.homeYaw) * spBC;
        spA8 = CosS(s.homeYaw) * spBC;
        spB4 = spAC + spA4;
        spB0 = spA8 + spA0;
        s.rollSpeed = sqrtf(spB4 * spB4 + spB0 * spB0);
        if (s.rollSpeed > 18.0f) s.rollSpeed = 18.0f;
        s.currentYaw = Atan2S_XY(spB0, spB4);
    }
    // func_808576BC: skidding (the ball's spin disagrees with its ground speed) -> slip sound and dust
    if (ground && !jumped) {
        int32_t v = (int32_t)(velPrev * CosS((int16_t)(yawPrev - s.currentYaw)) * 800.0f);
        v -= s.spin;
        v = v < 0 ? -v : v;
        if (v > 0x7D0) {
            if (v > 0x1770) s.sfx |= SFX_SLIP;                    // NA_SE_PL_GORON_SLIP - SFX_FLAG
            s.sfx |= SFX_DUST;                                    // dust spawned on ground: NA_SE_PL_ROLL_DUST - SFX_FLAG
        }
    }
    if (ground && !jumped) {
        s.linearVelocity = s.rollSpeed * CosS(0);            // grounded: unk_B08 * cos(floorPitch)
    } else if (!ground) {                                     // the air branch
        ScaledStepToS(&s.leanZ, 0, 0x190);
        s.wobble = 0;
        if (s.spikes == 0) {
            s.b48 += s.velY * 0.005f;
            if (s.phase == 1) {
                if (s.velY > 0.0f) {
                    if (s.velY + s.gravity < 0.0f) s.velY = -s.gravity;   // the apex lands exactly on 0
                } else {
                    s.b8a = 10;
                    if (s.velY > -1.0f) s.gravity = -0.2f;   // hang
                    else s.gravity = -10.0f;                 // slam (unk_3D0.unk_00 = 1)
                }
            }
            s.rollSpeed = s.linearVelocity;
        }
    }

    ScaledStepToS(&s.shapeYaw, s.homeYaw, 0x7D0);
    int16_t target = (int16_t)(spDC > 32767 ? 32767 : (spDC < -32768 ? -32768 : spDC));
    AsymStepToS(&s.spin, target, (spDC >= 0) ? 0x7D0 : 0x3E8, 0x4B0);
    if (s.spin != 0) {
        int16_t spD8 = s.spinAngle;
        s.spinAngle += s.spin;
        ScaledStepToS(&s.wobble, 0, (int16_t)Abs(s.spin));
        if (!s.airborne && (int64_t)((int32_t)s.spin + spD8) * spD8 <= 0) {   // grounded: once per revolution
            s.sfx |= SFX_ROLL;                                    // GORON_CHG_ROLL when spiked, synced to unk_B08
            s.rollSfxParam = s.rollSpeed;
        }
    }
    s.prevVelocity = s.linearVelocity;
    s.prevYaw = s.currentYaw;
    // Player_SetCylinderForAttack (end of Player_Action_96)
    if (s.phase == 2) { s.attack = ATK_GORON_POUND; s.attackRadius = 60.0f; }   // (DMG_GORON_POUND, 4, 60) + impact
    else if (s.spikes != 0) { s.attack = ATK_GORON_SPIKES; s.attackRadius = 25.0f; }   // (DMG_GORON_SPIKES, 1, 25)
    else { s.attack = ATK_NORMAL_ROLL; s.attackRadius = 25.0f; }                       // (DMG_NORMAL_ROLL, 1, 25)
    // Actor_MoveWithGravity (after the action): velocity.y += gravity, terminal velocity, pos += vel * 1.5
    if (s.airborne) {
        s.velY += s.gravity;
        if (s.velY < kTerminalVelocity) s.velY = kTerminalVelocity;
        s.posY += s.velY * kActorMovementScale;
        if (s.posY <= 0.0f) {                                 // back on the floor it left (no horizontal motion)
            s.posY = 0.0f; s.velY = 0.0f; s.airborne = false; s.landed = true;
        }
    }
}

}  // namespace goron_roll
