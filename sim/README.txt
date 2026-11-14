XLO-ZNS: further implementation of the simulator (Sections 5.9-5.11 of the paper)

This is a re-implementation written from the model description in the paper (Sections 3-4).
It is not the simulator that produced Sections 5.2-5.8; it reproduces that simulator's operating
point to within 4% (Table 8 of the paper) and is the source of every number in Sections 5.9-5.11.

Files
  sim.py         discrete-event simulator (policies: base, naive, strict, finish, cap, static, epfin)
  drive.py       runs a named set of simulations in parallel -> results/<set>.jsonl
  agg.py         means, 95% intervals (Student's t) and paired per-seed differences
  nums.py        derives every number quoted in Sections 5.9-5.11 -> nums.json
  fig_retire.py  draws Figure 7 (fig-retire.png) from results/ret.jsonl
  fig-retire.png the figure
  results/       output of the 409 runs reported in the paper
  nums.json      the derived numbers

Run (Python 3, no dependencies except matplotlib for the figure)
  python3 sim.py finish 1                       one run, JSON on stdout
  python3 sim.py naive 1 retire=3               retire the class of rank 4 four seconds into the measured interval
  python3 sim.py cap 1 idle_free=1 hold=1       capped rule, idle-zone accounting, reset-aware hold
  for s in op hold rem ret extra idle best; do python3 drive.py $s; done     all 409 runs (about 45 minutes on 2 cores)
  python3 nums.py && python3 fig_retire.py

Sets
  op     operating point, 4 policies x 8 seeds            hold   the reset-aware hold
  rem    cap / static / epfin, MOR 8 and 28, 3 and 32 classes
  ret    retirement of one class, and control runs        extra  rotation every 96 MiB, 16 classes
  idle   idle-zone accounting at MOR 14 and 28            best   cap + idle + hold; 3 classes; retirement with cap

Not implemented: the aging family (omega, kappa) and the single-context and key-indexed engines.
The 18 epfin runs of set "rem" end with "placement stalled"; that is the result (Section 5.11).
