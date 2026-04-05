# ALU Design Specification

## Overview
Design a 4-bit Arithmetic Logic Unit (ALU) supporting basic arithmetic and logic operations.

## Operations
- ADD: A + B
- SUB: A - B
- AND: A & B
- OR: A | B
- XOR: A ^ B
- NOT: ~A

## Interface

### Inputs
- clk: 1-bit clock
- rst_n: 1-bit active-low reset
- a[3:0]: 4-bit operand A
- b[3:0]: 4-bit operand B
- op[2:0]: 3-bit operation select
  - 000: ADD
  - 001: SUB
  - 010: AND
  - 011: OR
  - 100: XOR
  - 101: NOT (A only)

### Outputs
- result[3:0]: 4-bit operation result
- zero: 1-bit zero flag (asserted when result is 0)
- carry: 1-bit carry output (for arithmetic operations)

## Target KPIs
- Frequency: >= 100 MHz
- Area: <= 500 gates
- Power: <= 1 mW @ 100MHz
