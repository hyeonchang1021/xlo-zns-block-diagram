// XLO-ZNS placement path, register-transfer level.
// Implements the per-batch decision path of the schematic: IN, KEYEP, ZTBL, MATCH, SEL,
// ADMIT, ZUPD, RES, the reclaim gate of RECL and the free-zone pool.
// Not implemented here: the AES-XTS engine, victim selection and relocation (testbench model).
// Lengths and write pointers are counted in 4 KiB XTS units. NZ must equal 2**ZIDW.
`default_nettype none
module xlo_place #(
  parameter NSLOT    = 14,     // open-zone slots (MOR)
  parameter SELW     = 4,
  parameter CAP      = 16384,  // zone capacity: 64 MiB / 4 KiB
  parameter WPW      = 15,
  parameter LENW     = 8,
  parameter LMIN     = 8,      // smallest batch: 32 KiB
  parameter ROT      = 6144,   // key rotation spacing: 24 MiB
  parameter ROTW     = 13,
  parameter EPW      = 8,
  parameter NZ       = 256,
  parameter ZIDW     = 8,
  parameter LO_TH    = 32,     // NZ/8: discretionary reclaim below this
  parameter FORCE_TH = 5,      // NZ/48 = 5.33: mandatory reclaim at or below this
  parameter MAR      = 32
)(
  input  wire              clk,
  input  wire              rst,
  // application batch
  input  wire              app_vld,
  output wire              app_rdy,
  input  wire [LENW-1:0]   app_len,
  input  wire [2:0]        app_c,
  // relocation batch from the reclaim sequencer
  input  wire              rel_vld,
  output wire              rel_rdy,
  input  wire [LENW-1:0]   rel_len,
  input  wire [2:0]        rel_c,
  // registers written by host software once per epoch
  input  wire              epoch_tick,
  input  wire [7:0]        bk_safe,
  input  wire              sigma_lt_half,
  input  wire [15:0]       backlog_us,
  // zone handed back to the free pool after its reset completed
  input  wire              rz_vld,
  input  wire [ZIDW-1:0]   rz_zid,
  // decision for the batch held in BATCH_REG
  output wire              dec_vld,
  output wire              hit,
  output wire              admit,
  output wire [SELW-1:0]   sel,
  output wire [1:0]        tier,
  output wire              bk,
  output wire              viol,
  output wire              rot,
  output wire [EPW-1:0]    e,
  output wire [ZIDW-1:0]   dec_zid,
  output wire [WPW:0]      wp_n,
  output wire              fin,
  // zone lifecycle
  output wire              seal_vld,
  output wire [ZIDW-1:0]   seal_zid,
  output wire              reset_go,
  output wire [ZIDW:0]     free_cnt,
  output reg  [7:0]        bk_acc
);
  localparam [2:0] ZSE = 3'd0, ZSIO = 3'd1, ZSEO = 3'd2, ZSC = 3'd3, ZSF = 3'd4;

  // ---------------- IN: batch mux + register ----------------
  reg              b_vld;
  reg [LENW-1:0]   b_len;
  reg [2:0]        b_c;
  wire             in_rdy = ~b_vld | admit;
  wire             in_vld = rel_vld | app_vld;
  assign rel_rdy = in_rdy;
  assign app_rdy = in_rdy & ~rel_vld;
  always @(posedge clk) begin
    if (rst) b_vld <= 1'b0;
    else if (in_rdy) begin
      b_vld <= in_vld;
      if (in_vld) begin
        b_len <= rel_vld ? rel_len : app_len;
        b_c   <= rel_vld ? rel_c   : app_c;
      end
    end
  end
  assign dec_vld = b_vld;

  // ---------------- KEYEP: key-epoch file ----------------
  reg [ROTW-1:0] rot_cnt [0:7];
  reg [EPW-1:0]  key_ep  [0:7];
  wire [ROTW:0]  ksum = {1'b0, rot_cnt[b_c]} + b_len;
  assign rot = (ksum >= ROT);
  assign e   = key_ep[b_c];
  integer k;
  always @(posedge clk) begin
    if (rst) begin
      for (k = 0; k < 8; k = k + 1) begin rot_cnt[k] <= {ROTW{1'b0}}; key_ep[k] <= {EPW{1'b0}}; end
    end else if (admit) begin
      rot_cnt[b_c] <= rot ? {ROTW{1'b0}} : ksum[ROTW-1:0];
      if (rot) key_ep[b_c] <= key_ep[b_c] + 1'b1;
    end
  end

  // ---------------- ZTBL: open-zone table (flattened) ----------------
  reg [NSLOT*ZIDW-1:0] s_zid;
  reg [NSLOT*3-1:0]    s_st;
  reg [NSLOT*WPW-1:0]  s_wp;
  reg [NSLOT-1:0]      s_bnd;
  reg [NSLOT*3-1:0]    s_cls;
  reg [NSLOT*EPW-1:0]  s_ep;

  // ---------------- MATCH: one comparison per slot ----------------
  reg [NSLOT-1:0] fit, t1, t2, t3, zsf;
  reg [WPW:0]     wsum;
  integer i;
  always @* begin
    for (i = 0; i < NSLOT; i = i + 1) begin
      wsum   = {1'b0, s_wp[i*WPW +: WPW]} + b_len;
      fit[i] = (wsum <= CAP);
      t1[i]  = fit[i] &  s_bnd[i] & (s_cls[i*3 +: 3] == b_c) & (s_ep[i*EPW +: EPW] == e);
      t2[i]  = fit[i] & ~s_bnd[i];
      t3[i]  = fit[i] &  s_bnd[i] & (s_cls[i*3 +: 3] == b_c) & (s_ep[i*EPW +: EPW] != e);
      zsf[i] = (s_st[i*3 +: 3] == ZSF);
    end
  end

  // ---------------- SEL: tier select ----------------
  wire any1 = |t1, any2 = |t2, any3 = |t3, any4 = |fit;
  assign hit  = b_vld & any4;
  assign tier = any1 ? 2'd0 : any2 ? 2'd1 : any3 ? 2'd2 : 2'd3;
  reg [SELW-1:0] idx1, idx2, idx3, idx4, idxmax, rf_idx;
  reg [WPW-1:0]  minwp, maxwp;
  reg            f4;
  integer j;
  always @* begin
    idx1 = 0; idx2 = 0; idx3 = 0; idx4 = 0; idxmax = 0; rf_idx = 0;
    minwp = {WPW{1'b1}}; maxwp = {WPW{1'b0}}; f4 = 1'b0;
    for (j = NSLOT - 1; j >= 0; j = j - 1) begin       // descending: ties go to the lowest slot
      if (t1[j])  idx1 = j;
      if (t2[j])  idx2 = j;
      if (t3[j])  idx3 = j;
      if (zsf[j]) rf_idx = j;
      if (fit[j] && (!f4 || s_wp[j*WPW +: WPW] <= minwp)) begin idx4 = j; minwp = s_wp[j*WPW +: WPW]; f4 = 1'b1; end
      if (s_wp[j*WPW +: WPW] >= maxwp) begin idxmax = j; maxwp = s_wp[j*WPW +: WPW]; end
    end
  end
  assign sel = (tier == 2'd0) ? idx1 : (tier == 2'd1) ? idx2 : (tier == 2'd2) ? idx3 : idx4;
  assign bk  = tier[1] & hit;
  assign dec_zid = s_zid[sel*ZIDW +: ZIDW];

  // ---------------- RES: MOR / MAR check ----------------
  reg [SELW:0] open_cnt, act_cnt;
  integer m;
  always @* begin
    open_cnt = 0; act_cnt = 0;
    for (m = 0; m < NSLOT; m = m + 1) begin
      if (s_st[m*3 +: 3] == ZSIO || s_st[m*3 +: 3] == ZSEO) open_cnt = open_cnt + 1'b1;
      if (s_st[m*3 +: 3] == ZSIO || s_st[m*3 +: 3] == ZSEO || s_st[m*3 +: 3] == ZSC) act_cnt = act_cnt + 1'b1;
    end
  end
  wire res_ok = (open_cnt <= NSLOT) & (act_cnt <= MAR);

  // ---------------- ADMIT: Bk budget ----------------
  assign admit = hit & res_ok;
  assign viol  = bk & (bk_acc >= bk_safe);
  always @(posedge clk) begin
    if (rst)             bk_acc <= 8'd0;
    else if (epoch_tick) bk_acc <= 8'd0;
    else if (bk && bk_acc != 8'hFF) bk_acc <= bk_acc + 1'b1;
  end

  // ---------------- free-zone pool ----------------
  reg [ZIDW:0]   boot_next;                 // zones never used since power-up
  reg [ZIDW-1:0] fq [0:NZ-1];               // zones returned by reclaim
  reg [ZIDW-1:0] fq_h, fq_t;
  reg [ZIDW:0]   fq_cnt;
  wire           boot_has = (boot_next != NZ);
  assign free_cnt = (NZ - boot_next) + fq_cnt;
  wire [ZIDW-1:0] pop_zid = boot_has ? boot_next[ZIDW-1:0] : fq[fq_h];

  // ---------------- ZUPD: zone update, finish and refill ----------------
  wire [WPW-1:0] wp_s = s_wp[sel*WPW +: WPW];
  assign wp_n = {1'b0, wp_s} + b_len;
  assign fin  = (wp_n > CAP - LMIN);                  // no batch can fit any more: finish the zone
  wire zsf_any    = |zsf;
  wire seal_nofit = b_vld & ~any4 & ~zsf_any;         // nothing fits: finish the fullest zone
  wire rf_vld     = zsf_any & (free_cnt != 0);        // swap a Full zone for an empty one
  assign seal_vld = rf_vld;
  assign seal_zid = s_zid[rf_idx*ZIDW +: ZIDW];
  integer n;
  always @(posedge clk) begin
    if (rst) begin
      for (n = 0; n < NSLOT; n = n + 1) begin
        s_zid[n*ZIDW +: ZIDW] <= n;
        s_st[n*3 +: 3]        <= ZSE;
        s_wp[n*WPW +: WPW]    <= {WPW{1'b0}};
        s_cls[n*3 +: 3]       <= 3'd0;
        s_ep[n*EPW +: EPW]    <= {EPW{1'b0}};
      end
      s_bnd <= {NSLOT{1'b0}};
    end else begin
      if (admit) begin
        s_wp[sel*WPW +: WPW] <= fin ? CAP[WPW-1:0] : wp_n[WPW-1:0];
        s_st[sel*3 +: 3]     <= fin ? ZSF : ZSIO;
        s_bnd[sel]           <= 1'b1;
        s_cls[sel*3 +: 3]    <= b_c;
        s_ep[sel*EPW +: EPW] <= e;
      end
      if (seal_nofit) begin
        s_wp[idxmax*WPW +: WPW] <= CAP[WPW-1:0];
        s_st[idxmax*3 +: 3]     <= ZSF;
      end
      if (rf_vld) begin
        s_zid[rf_idx*ZIDW +: ZIDW] <= pop_zid;
        s_st[rf_idx*3 +: 3]        <= ZSE;
        s_wp[rf_idx*WPW +: WPW]    <= {WPW{1'b0}};
        s_bnd[rf_idx]              <= 1'b0;
      end
    end
  end
  always @(posedge clk) begin
    if (rst) begin
      boot_next <= NSLOT; fq_h <= {ZIDW{1'b0}}; fq_t <= {ZIDW{1'b0}}; fq_cnt <= {(ZIDW+1){1'b0}};
    end else begin
      if (rf_vld) begin
        if (boot_has) boot_next <= boot_next + 1'b1;
        else          fq_h      <= fq_h + 1'b1;
      end
      if (rz_vld) begin fq[fq_t] <= rz_zid; fq_t <= fq_t + 1'b1; end
      fq_cnt <= fq_cnt + (rz_vld ? 1'b1 : 1'b0) - ((rf_vld & ~boot_has) ? 1'b1 : 1'b0);
    end
  end

  // ---------------- RECL: reclaim gate ----------------
  wire lo     = (free_cnt <  LO_TH);
  wire force_ = (free_cnt <= FORCE_TH);
  wire busy   = (backlog_us > 16'd4000);
  wire defer  = busy & sigma_lt_half & ~force_;
  assign reset_go = force_ | (lo & ~defer);
endmodule
`default_nettype wire
