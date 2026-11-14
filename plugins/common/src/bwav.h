// BWAV (Switch stream wave): channel records and Nintendo DSP-ADPCM, for sounds built at runtime from the game's own.
//
// Header: "BWAV", BOM u16, version u16, CRC u32, prefetch flag u16, channel count u16; then one 0x4c record per
// channel: codec u16 (0 PCM16, 1 DSP-ADPCM), pan u16 (0 left, 1 right, 2 centre), rate u32, samples u32, prefetch
// samples u32, 16 i16 coefficients (8 predictor pairs), data offset in the stream file u32, data offset in this file
// u32, loop flag, loop end, loop start, then predictor/scale and history words.
// A streamed sound is played from the BARS's prefetch BWAV (header + the first samples, a byte prefix of each
// channel's stream data) and then read from Stream/<name>.bwav at the offsets and with the codec and coefficients
// that the prefetch header gives.
#pragma once
#include "gamefiles.h"
#include <cstdint>
#include <vector>

namespace bwav {

struct Channel {
    uint16_t codec = 0, pan = 0;
    uint32_t rate = 0, samples = 0, prefetch = 0;
    int16_t coefs[16] = {};
    uint32_t offStream = 0, offHere = 0;
};
constexpr size_t kRecord = 0x4c;

// The channel records. False when this is not a BWAV.
bool Channels(const gamefiles::Bytes& file, std::vector<Channel>& out);

// One channel's samples (PCM16 or DSP-ADPCM from its data in this file). Empty on error.
std::vector<int16_t> Decode(const gamefiles::Bytes& file, const Channel& ch);

// DSP-ADPCM, 8-byte frames of 14 samples. hist1/hist2: the decoder history (last, second last sample) going in;
// updated to the history after the last sample.
void DecodeDsp(const uint8_t* data, size_t bytes, const int16_t coefs[16], size_t n, int16_t* out, int& hist1, int& hist2);
// Encodes with a given coefficient table: each frame takes the predictor and scale with the least squared error,
// decoded exactly as DecodeDsp does (so the history never drifts). Starts from hist1/hist2 and updates them.
// n is rounded up to whole frames (the tail padded with zeros).
gamefiles::Bytes EncodeDsp(const int16_t* pcm, size_t n, const int16_t coefs[16], int& hist1, int& hist2);

}  // namespace bwav
