This project implements a RISC-V (RV32IM) CPU in Verilog across six progressive undergraduate labs,
each building a different microarchitectural variant. All labs share common building blocks
(ALU, ImmGen, Regs, RAM, comparator, pipeline registers) but differ in pipeline organization,
hazard handling, and execution model. You must implement the hollowed-out modules in every lab.

Your working directory is `/workspace`. The project code is under `/workspace/repo/src/`.

Lab 1 — Basic 5-Stage In-Order Pipeline (`src/lab1/`):

Classic IF → ID → EX → MEM → WB pipeline for RV32I. The ID stage resolves branches
and contains forwarding muxes.

Modules to implement:
ALU (`core/ALU.v`): 12-operation ALU (ADD, SUB, AND, OR, XOR, SLL, SRL, SRA, SLT,
  SLTU, A+4, B-passthrough) with zero and overflow flags.
ImmGen (`core/ImmGen.v`): Sign-extend immediates for I/B/J/S/U formats.
RAM_B (`core/RAM_B.v`): Byte-addressable data memory with byte/half/word access.
Regs (`core/Regs.v`): 31-entry register file with write-through, dual read ports.
REG_IF_ID (`core/REG_IF_ID.v`): IF/ID latch with stall and flush support.
REG_ID_EX (`core/REG_ID_EX.v`): ID/EX latch with flush-on-hazard support.
REG_EX_MEM (`core/REG_EX_MEM.v`): EX/MEM pipeline register.
REG_MEM_WB (`core/REG_MEM_WB.v`): MEM/WB pipeline register.
cmp_32 (`common/cmp_32.v`): 6-mode comparator (EQ, NE, LT, LTU, GE, GEU).
CtrlUnit (`core/CtrlUnit.v`): Full RV32I control signal generation.
HazardDetectionUnit (`core/HazardDetectionUnit.v`): Forwarding and stall logic.
RV32core (`core/RV32core.v`): Top-level MUX wiring (PC select, forwarding, ALU inputs).

Lab 2 — Pipeline with CSR and Exceptions (`src/lab2/`):

Same 5-stage pipeline as lab1, extended with CSR registers and an exception unit.

Additional modules beyond the lab1 set:
CSRRegs (`core/CSRRegs.v`): CSR register file supporting CSRRW/CSRRS/CSRRC.
ExceptionUnit (`core/ExceptionUnit.v`): Exception handling with PC redirect.
All lab1-equivalent modules (ALU, CtrlUnit, HazardDetectionUnit, ImmGen, Regs, RAM_B,
  pipeline registers, RV32core) must also be implemented with CSR/exception extensions.

Lab 3 — Pipeline with UART and Extended Hazards (`src/lab3/`):

5-stage pipeline with UART simulation support in RAM and extended control signals.

Same module set as lab1, plus:
RAM_B includes simulated UART output at address 0x10000000.
CtrlUnit includes CSR read/write signals and exception vector generation.
cmp_32 (`common/cmp_32.v`): Same comparator as lab1.

Lab 4 — Pipeline Variant (`src/lab4/`):

Another 5-stage in-order pipeline variant with a different control signal set.

Same module set as lab1 (ALU, CtrlUnit, HazardDetectionUnit, ImmGen, Regs,
pipeline registers, RV32core). Differences in control signal encoding.

Lab 5 — Out-of-Order with Scoreboard (`src/lab5/`):

Out-of-order execution using a scoreboard-based controller with dedicated functional units.
Pipeline: IF → Issue → Read Operands → Execute (multiple FUs) → Write-Back.

Modules to implement:
FU_ALU (`core/FU_ALU.v`): Stateful ALU FU — latches inputs on EN, produces result
  in one cycle. Must implement state machine and full ALU computation.
FU_mul (`core/FU_mul.v`): Multiplier FU with 64-cycle latency (shifting state register).
  Instantiate `multiplier` IP, return lower 32 bits.
FU_div (`core/FU_div.v`): Divider FU using AXI handshaking (valid/ready/data).
  Instantiate `divider` IP, return quotient from upper 32 bits.
FU_jump (`core/FU_jump.v`): Jump/branch FU — compare operands with `cmp_32`,
  compute target PC = (JALR ? rs1 : PC) + imm, return address = PC + 4.
FU_mem (`core/FU_mem.v`): Memory FU with 2-cycle latency. Compute address via
  `add_32`, instantiate `RAM_B` for memory access.

Lab 6 — Out-of-Order with Tomasulo/Reservation Stations (`src/lab6/`):

Full Tomasulo OoO execution with reservation stations, CDB broadcast, and RAT.

Modules to implement:
FU_ALU/FU_mul/FU_div/FU_jump/FU_mem: Same functional units as lab5.
CtrlUnit (`core/CtrlUnit.v`): Instruction decode with FU routing and op encoding.
Regs (`core/Regs.v`): Register file with RAT (Register Alias Table) for Tomasulo.
  Implement `rs_num_A`/`rs_num_B` RAT lookups and CDB broadcast writeback.
RAM_B (`core/RAM_B.v`): Extended RAM with UART simulation and larger address space.
ImmGen (`core/ImmGen.v`): Immediate generator (same as other labs).
RV32core (`core/RV32core.v`): Top-level wiring for Tomasulo — PC enable, issue enable,
  RS allocation, reservation station port connections, FU port connections, CDB broadcast,
  and stall logic.

The tests also depend on these signatures and entry points:

The test suite compiles Verilog sources with iverilog and runs simulation via a test program
pre-loaded into instruction ROM. Tests verify register write-back values at specific PCs.
The testbench monitors WB-stage signals via hierarchical access to the core module.
