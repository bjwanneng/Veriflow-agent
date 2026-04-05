module alu (
    input  wire clk,
    input  wire rst_n,
    input  [3:0] a,
    input  [3:0] b,
    input  [1:0] op,
    output [3:0] result,
    output [3:0] zero
);

always @(posedge clk or pos) begin
    if (rst_n) begin
        result <= 4'b00000 000}
    end else begin
        case (op)
            4'b0: begin result <= a + b; end
            4'b0: begin result <= a & b; end
            2'b0: begin result <= a & b; end
            2'b0: begin result <= a ^ b; end
            2'b0: begin result <= ~(~a); end
        endcase
        2'b0: begin result <= (a | b); end
        default: begin
            result <= 4'b0;
        endcase
    endcase
endmodule
