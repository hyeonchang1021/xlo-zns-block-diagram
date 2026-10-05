// Self-logging testbench for xlo_place. Stimulus and host-register values come from hex files
// written by gen_stim.py; every decision is logged with the full slot table so check.py can
// re-derive it from the paper's rule. Victim selection and zone reset are modelled here.
`timescale 1ns/1ps
module tb;
  parameter NSLOT = 14, SELW = 4, CAP = 16384, WPW = 15, LMIN = 8, ROT = 6144, ROTW = 13;
  parameter NZ = 256, ZIDW = 8, LO_TH = 32, FORCE_TH = 5;
  parameter NB = 1000, NE = 64, TICK = 40, RDELAY = 6;
  localparam LENW = 8, EPW = 8;

  reg clk = 0, rst = 1;
  always #5 clk = ~clk;

  reg [11:0] stim [0:NB-1];     // {is_rel, len[7:0], c[2:0]}
  reg [24:0] host [0:NE-1];     // {bk_safe[7:0], sigma_lt_half, backlog_us[15:0]}
  integer bi = 0, cyc = 0;
  wire [11:0] cur  = stim[bi < NB ? bi : NB - 1];
  wire        have = (bi < NB) & ~rst;
  wire        app_vld = have & ~cur[11], rel_vld = have & cur[11];
  wire        app_rdy, rel_rdy;
  wire        epoch_tick = ((cyc % TICK) == TICK - 1);
  wire [24:0] hv = host[(cyc / TICK) % NE];

  reg              rz_vld = 0;
  reg [ZIDW-1:0]   rz_zid = 0;
  wire             dec_vld, hit, admit, bk, viol, rot, fin, seal_vld, reset_go;
  wire [SELW-1:0]  sel;
  wire [1:0]       tier;
  wire [EPW-1:0]   e;
  wire [ZIDW-1:0]  dec_zid, seal_zid;
  wire [WPW:0]     wp_n;
  wire [ZIDW:0]    free_cnt;
  wire [7:0]       bk_acc;

  xlo_place #(.NSLOT(NSLOT), .SELW(SELW), .CAP(CAP), .WPW(WPW), .LMIN(LMIN), .ROT(ROT), .ROTW(ROTW),
              .NZ(NZ), .ZIDW(ZIDW), .LO_TH(LO_TH), .FORCE_TH(FORCE_TH)) dut (
    .clk(clk), .rst(rst),
    .app_vld(app_vld), .app_rdy(app_rdy), .app_len(cur[10:3]), .app_c(cur[2:0]),
    .rel_vld(rel_vld), .rel_rdy(rel_rdy), .rel_len(cur[10:3]), .rel_c(cur[2:0]),
    .epoch_tick(epoch_tick), .bk_safe(hv[24:17]), .sigma_lt_half(hv[16]), .backlog_us(hv[15:0]),
    .rz_vld(rz_vld), .rz_zid(rz_zid),
    .dec_vld(dec_vld), .hit(hit), .admit(admit), .sel(sel), .tier(tier), .bk(bk), .viol(viol),
    .rot(rot), .e(e), .dec_zid(dec_zid), .wp_n(wp_n), .fin(fin),
    .seal_vld(seal_vld), .seal_zid(seal_zid), .reset_go(reset_go), .free_cnt(free_cnt), .bk_acc(bk_acc));

  // reclaim model: reset the oldest sealed zone RDELAY cycles after reset_go
  reg [ZIDW-1:0] sq [0:NZ-1];
  integer sq_h = 0, sq_t = 0, pend = 0;
  reg [ZIDW-1:0] pz;
  integer s, idle = 0;

  initial begin
    $readmemh("stim.hex", stim);
    $readmemh("host.hex", host);
    repeat (3) @(posedge clk);
    rst <= 0;
  end

  always @(posedge clk) if (!rst) begin
    // values read here are the ones the DUT registers sample at this edge
    $display("G %0d %0d %0d %0d %0d %0d %0d %0d", cyc, free_cnt, hv[15:0], hv[16], reset_go, hv[24:17], bk_acc, epoch_tick);
    if (dec_vld) begin
      $write("D %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d",
             cyc, dut.b_c, dut.b_len, e, rot, hit, tier, sel, bk, viol, admit, fin, wp_n, dut.seal_nofit, dut.idxmax);
      for (s = 0; s < NSLOT; s = s + 1)
        $write(" %0d:%0d:%0d:%0d:%0d:%0d", dut.s_zid[s*ZIDW +: ZIDW], dut.s_st[s*3 +: 3], dut.s_wp[s*WPW +: WPW],
               dut.s_bnd[s], dut.s_cls[s*3 +: 3], dut.s_ep[s*EPW +: EPW]);
      $write("\n");
    end
    if (seal_vld) $display("R %0d %0d %0d %0d", cyc, dut.rf_idx, seal_zid, dut.pop_zid);
    if (rz_vld)   $display("Z %0d %0d", cyc, rz_zid);

    if (have && (cur[11] ? rel_rdy : app_rdy)) bi <= bi + 1;
    cyc <= cyc + 1;

    rz_vld <= 0;
    if (seal_vld) begin sq[sq_t % NZ] <= seal_zid; sq_t <= sq_t + 1; end
    if (pend > 0) begin
      pend <= pend - 1;
      if (pend == 1) begin rz_vld <= 1; rz_zid <= pz; end
    end else if (reset_go && sq_h != sq_t) begin
      pz <= sq[sq_h % NZ]; sq_h <= sq_h + 1; pend <= RDELAY;
    end

    if (bi >= NB && !dec_vld) idle <= idle + 1;
    if (idle > 20) begin $display("END %0d %0d", cyc, bi); $finish; end
    if (cyc > 40 * NB + 100000) begin $display("TIMEOUT %0d %0d", cyc, bi); $finish; end
  end
endmodule
