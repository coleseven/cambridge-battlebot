#pragma once
// Patchable genome block. tools/neat_train.py finds the magic words in the compiled .wasm and
// overwrites the payload in place (no recompile per genome).
//
// Layout (32-bit words; ints stored as uint32, floats as their IEEE bits):
//   [0..1] magic 'NEAT' 'BLOB'   [2] capacity in words   [3] format version (1)
//   [4] resign round (0 = never; team A resigns at that round to end duels early)
//   [5..16] word offset of the network program for champion_type 0..11 (0 = none -> linear prior)
// Program at offset o:
//   [n_evals] then per eval: [node_slot] [activation] [bias f] [response f] [k] then k x ([src_slot] [weight f])
//   then [output_slot]. Slots 0..NF-1 are the option features; hidden/output nodes use NF and up.
// Activations: 0 identity, 1 tanh(2.5z), 2 sigmoid(5z), 3 relu (neat-python conventions).
#include <cstdint>

constexpr std::uint32_t GENOME_WORDS = 16384;
constexpr int MAX_NODES = 64;
constexpr int MAX_LINKS = 160;
extern "C" volatile std::uint32_t GENOME_BLOB[GENOME_WORDS];
