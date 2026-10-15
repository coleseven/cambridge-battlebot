#include "genome.h"

extern "C" {
alignas(16) volatile std::uint32_t GENOME_BLOB[GENOME_WORDS] = {0x4E454154u, 0x424C4F42u, GENOME_WORDS, 1u, 0u};
}
