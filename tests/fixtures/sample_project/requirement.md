# Simple ALU Design

Design a 4-bit ALU that supports the following operations:
- ADD: result = A + B
- SUB: result = A - B
- AND: result = A & B
- OR: result = A | B
- XOR: result = A ^ B
- NOT: result = ~A

## Interface

Inputs:
- clk: 1-bit clock
- rst_n: 1-bit active-low reset
- a [3:0]: 4-bit input A
- b [3:0]: 4-bit input B
- op [2:0]: 2-bit operation select (00=ADD, 01=SUB, 10=AND, 11=OR, 12=XOR)

Outputs:
- result [3:0]: 4-bit output
- zero [3:0]: 4-bit zero flag ( all zeros when rst_n is1

## Target KPIs
- Frequency: 100 MHz
- Max cells: 500
