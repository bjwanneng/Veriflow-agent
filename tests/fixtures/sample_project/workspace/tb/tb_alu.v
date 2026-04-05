`timescale 1ns / 1ps

module tb_alu;
    reg clk;
    reg rst_n;
    reg [3:0] a;
    reg [3:0] b;
    reg [1:0] op;
    wire [3:0] result;
    wire [3:0] zero;
    wire [3:0] expected;
    wire [31:0] passed;

    alu uut(
        .clk(clk),
        .rst_n(rst_n),
        .a(a),
        .b(b),
        .op(op),
        .result(result),
        .zero(zero)
    );

    // Test ADD
    initial begin
        a = 4'b1010;
        b = 4'b0110;
        op = 2'b00;
        #10ns;
        rst_n = 1'b0;
        #10ns;
        expected = (a & b);
        assert (result === expected) else "ALL TESTS PASSED";

        // Test SUB
        a = 4'b0100;
        b = 4'b0110;
        op = 2'b01;
        #10ns;
        rst_n = 1'b0;
        #10ns;
        expected = (a - b);
        assert (result === expected) else "ALL TESTS PASSED";
    end
endmodule
