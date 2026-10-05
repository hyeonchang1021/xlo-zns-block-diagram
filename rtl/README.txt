XLO-ZNS placement path, Verilog implementation

Files
  xlo_place.v   RTL of the decision path (synthesizable Verilog-2005)
  tb_xlo.v      testbench; logs every decision with the full slot table
  gen_stim.py   writes stim.hex and host.hex
  check.py      re-derives every decision from the paper's rule and compares

Run (Icarus Verilog 12, Python 3)
  python3 gen_stim.py 120000 8192 11 paper
  iverilog -g2005 -o tb.vvp -Ptb.NB=120000 -Ptb.NE=8192 tb_xlo.v xlo_place.v
  vvp -n tb.vvp > run.log
  python3 check.py run.log stim.hex            # exit 0 and no "errors" key = pass

Stress configuration
  python3 gen_stim.py 120000 8192 23 stress
  iverilog -g2005 -o tb.vvp -Ptb.NB=120000 -Ptb.NE=8192 -Ptb.CAP=512 -Ptb.ROT=300 -Ptb.WPW=10 tb_xlo.v xlo_place.v
  vvp -n tb.vvp > run.log
  python3 check.py run.log stim.hex CAP=512 ROT=300

Synthesis (Yosys 0.33, nextpnr-ice40)
  yosys -p "read_verilog xlo_place.v; synth_ice40 -top xlo_place -json xlo.json"
  nextpnr-ice40 --hx8k --package ct256 --json xlo.json --freq 25

Not implemented in RTL: AES-XTS engine, victim selection, relocation FSM.
The testbench returns the oldest sealed zone 6 cycles after reset_go.
