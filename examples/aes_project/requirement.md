# AES-128 Encryption Core

Design a synthesizable AES-128 encryption core in Verilog.

## Interface
- clk, rst_n (active-low), start, plaintext[127:0], key[127:0]
- ciphertext[127:0], done

## Requirements
- Full AES-128 with 10 rounds: SubBytes, ShiftRows, MixColumns, AddRoundKey
- Key expansion generates 11 round keys
- Iterative architecture (area-efficient)
- S-Box as combinational lookup

## Target: 100MHz, ≤5000 cells
