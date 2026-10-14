This project implements three RISC-V-style CPUs in Verilog: a single-cycle processor, a basic 5-stage pipeline, and a full pipeline with hazard hardware. The ISA uses a custom 5-bit opcode encoding (not standard RISC-V); see the comments in `Computer.v` for the instruction set.

Your working directory is `/workspace`. The project code is already present across three architectures: `Single_Cycle/`, `Basic_Pipeline/`, and `Final_Pipeline/`. Multiple modules have been stubbed out with `/* TODO: implement */` or `// TODO: implement` and must be completed so that all tests pass.

Implement the following modules:

Single_Cycle (single-cycle processor):

ALU (`Single_Cycle/ALU.v`): Combinational ALU supporting 7 operations selected by a 3-bit operator signal. Must produce a 32-bit result and drive comparison/zero flags. (~1 module, core datapath logic)

Controller (`Single_Cycle/Controller.v`): Decodes a 5-bit opcode into a 12-bit control signal bundle that drives register writes, memory access, ALU operation selection, and address/data multiplexer selects. (~1 module, control logic)

ImmGen (`Single_Cycle/ImmGen.v`): Extracts and sign-extends the immediate value from the 32-bit instruction word based on the opcode. Handles multiple instruction formats (I-type, S-type, B-type, U-type, J-type) with different bit-field layouts. (~1 module, immediate decoding)

AddrMUX (`Single_Cycle/AddrMUX.v`): Selects the next PC address from sequential, branch-relative, jump-relative, or register-relative sources based on a 3-bit selector and branch condition flags. (~1 module, PC address routing)

DataMUX (`Single_Cycle/DataMUX.v`): Selects the write-back data to the register file from ALU result, immediate, memory data, or return address based on a 2-bit selector. (~1 module, write-back mux)

Reg (`Single_Cycle/Reg.v`): 32-entry 32-bit register file with dual read ports, one write port, synchronous reset, and a display output port (R31). (~1 module, register storage)

DMem (`Single_Cycle/DMem.v`): 32-entry data memory with synchronous write, asynchronous read gated by a read-enable signal, and synchronous reset. (~1 module, data storage)

Basic_Pipeline (5-stage pipeline without hazard handling):

ALU (`Basic_Pipeline/ALU.v`): Combinational ALU for the basic pipeline, supporting the same 7 operations as Single_Cycle. Must produce a 32-bit result and drive comparison/zero flags. (~1 module, core datapath logic)

Controller (`Basic_Pipeline/Controller.v`): Decodes a 5-bit opcode into a 12-bit control signal bundle for the basic pipeline. Same opcode-to-control mapping as Single_Cycle. (~1 module, control logic)

ImmGen (`Basic_Pipeline/ImmGen.v`): Extracts and sign-extends the immediate value from the 32-bit instruction word for the basic pipeline. Same format handling as Single_Cycle. (~1 module, immediate decoding)

AddrMUX (`Basic_Pipeline/AddrMUX.v`): Same address selection logic as Single_Cycle, used in the pipeline context.

DataMUX (`Basic_Pipeline/DataMUX.v`): Same write-back data selection as Single_Cycle.

Reg (`Basic_Pipeline/Reg.v`): Register file for the pipeline (same interface as Single_Cycle).

DMem (`Basic_Pipeline/DMem.v`): Data memory for the pipeline (same interface as Single_Cycle).

IFIDReg (`Basic_Pipeline/IFIDReg.v`): IF/ID pipeline register that latches the PC address and instruction on clock edges, with synchronous reset. (~1 module, pipeline register)

IDEXReg (`Basic_Pipeline/IDEXReg.v`): ID/EX pipeline register that latches decoded register values, immediate, destination register, control signals, with synchronous reset. (~1 module, pipeline register)

EXMEMReg (`Basic_Pipeline/EXMEMReg.v`): EX/MEM pipeline register that latches ALU results, flags, data, control signals, with synchronous reset. (~1 module, pipeline register)

MEMWBReg (`Basic_Pipeline/MEMWBReg.v`): MEM/WB pipeline register that latches memory/ALU output for write-back, with synchronous reset. (~1 module, pipeline register)

Final_Pipeline (5-stage pipeline with full hazard handling):

ALU (`Final_Pipeline/ALU.v`): Combinational ALU for the final pipeline, supporting the same 7 operations. Must produce a 32-bit result and drive comparison/zero flags. (~1 module, core datapath logic)

Controller (`Final_Pipeline/Controller.v`): Decodes a 5-bit opcode into a 12-bit control signal bundle for the final pipeline. Same opcode-to-control mapping. (~1 module, control logic)

ImmGen (`Final_Pipeline/ImmGen.v`): Extracts and sign-extends the immediate value from the 32-bit instruction word for the final pipeline. Same format handling. (~1 module, immediate decoding)

PC (`Final_Pipeline/PC.v`): Program counter with flush and stall support. On flush, accepts the new address; on stall, holds current value; otherwise increments normally. (~1 module, PC with hazard control)

AddrMUX (`Final_Pipeline/AddrMUX.v`): Address multiplexer (same logic as Single_Cycle).

DataMUX (`Final_Pipeline/DataMUX.v`): Write-back data multiplexer (same logic as Single_Cycle).

Reg (`Final_Pipeline/Reg.v`): Register file (same as Single_Cycle but portOut is full 32-bit width of R31).

DMem (`Final_Pipeline/DMem.v`): Data memory (same as Single_Cycle but reset is active-low).

IFIDReg (`Final_Pipeline/IFIDReg.v`): IF/ID pipeline register with flush and stall support. On flush, clears outputs; on stall, holds current values; otherwise latches new instruction and address. (~1 module)

IDEXReg (`Final_Pipeline/IDEXReg.v`): ID/EX pipeline register with flush and stall support. Adds rs1/rs2 source register forwarding outputs. On flush or stall-with-bubble, clears all outputs; otherwise latches normally. (~1 module)

EXMEMReg (`Final_Pipeline/EXMEMReg.v`): EX/MEM pipeline register with flush support. On flush, clears all outputs; otherwise latches normally. (~1 module)

MEMWBReg (`Final_Pipeline/MEMWBReg.v`): MEM/WB pipeline register with synchronous reset. (~1 module)

FWDMUX (`Final_Pipeline/FWDMUX.v`): Forwarding data multiplexer that selects between original register data, EX/MA ALU result, EX/MA immediate, EX/MA return address, or MA/WB write-back data based on 3-bit forwarding control signals (one mux for each source operand). (~1 module)

FWD (`Final_Pipeline/FWD.v`): Forwarding unit that generates 3-bit forwarding select signals for both source operands by comparing pipeline register destinations with source registers. (~1 module)

Stall (`Final_Pipeline/Stall.v`): Stall detection unit that identifies load-use hazards and generates a stall signal. (~1 module)

Flush (`Final_Pipeline/Flush.v`): Flush control unit that detects taken branches and jumps and generates a flush signal to invalidate wrongly fetched instructions. (~1 module)

The tests also depend on these signatures and entry points:

The following modules are exercised directly by the test suite:

`ALU(Data1In, Data2In, OperatorIn, LessOut, ZeroOut, ResultOut)` — Computes arithmetic/logic result and flags
`Controller(opcodeIn, ctrSignalsOut)` — Decodes opcode to 12-bit control bundle
`ImmGen(InsIn, Imm32Out)` — Extracts and sign-extends immediate from instruction
`FWD(IDEXrs1In, IDEXrs2In, EXMAwriteIn, EXMArdIn, EXMADataSelectorIn, MAWBwriteIn, MAWBrdIn, forward1Out, forward2Out)` — Data forwarding control
`Stall(IFIDrs1In, IFIDrs2In, IDEXIn, IDEXrdIn, MEMWBRegWriteIn, MEMWBrdIn, stallOut)` — Load-use hazard detection
`Flush(lessIn, zeroIn, AddrSelectorIn, flushOut)` — Branch/jump flush control

The integration tests instantiate the full `Computer` module from `Single_Cycle/`, which requires all Single_Cycle modules to work correctly together.
