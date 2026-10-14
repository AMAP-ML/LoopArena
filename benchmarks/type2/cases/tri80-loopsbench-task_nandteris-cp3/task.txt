This project follows the classic from-Nand-to-general-purpose-computer curriculum. You must build a complete general-purpose computer from the ground up, starting from Nand gates and progressing through Boolean logic, arithmetic, sequential memory, machine language programming, and computer architecture. All source files with `// TODO: implement` stubs must be completed.

Your working directory is `/workspace`. The project code is already present. You need to implement all the modules and features listed below so that all tests pass.

Implement the following modules:

Boolean Logic (`projects/01/`): Implement all 15 combinational logic chips using only the built-in `Nand` gate as the base primitive. Chips include 1-bit gates (Not, And, Or, Xor), a multiplexer (Mux), a demultiplexer (DMux), 16-bit variants of each gate, an 8-input Or, and multi-way multiplexers/demultiplexers of various widths. (~15 HDL files)

Boolean Arithmetic (`projects/02/`): Implement adder circuits and an Arithmetic Logic Unit. The ALU accepts two 16-bit inputs and six control bits (zx, nx, zy, ny, f, no) and produces a 16-bit output along with two status flags (zr for zero, ng for negative). (~5 HDL files)

Sequential Logic (`projects/03/a/` and `projects/03/b/`): Implement clocked memory elements building from a 1-bit register up through a hierarchy of RAM chips. The `DFF` (D flip-flop) is available as a built-in primitive for sequential circuits. Chips include a 1-bit register, 16-bit register, and RAM modules from 8 words up through RAM512, RAM4K, and RAM16K. The 16-bit Program Counter supports increment, load, and reset operations. (~8 HDL files)

Machine Language (`projects/04/`, plus canonical Hack assembly examples in `projects/06/add/`, `projects/06/max/`, and `projects/06/rect/`): Write `Mult.asm` and `Fill.asm`, then restore the small sample programs `Add.asm`, `Max.asm`, `MaxL.asm`, `Rect.asm`, and `RectL.asm`. Together these files exercise repeated addition, screen-filling loops, branching, symbolic labels, and raw numeric addressing in the Hack instruction set. (~7 ASM files)

Computer Architecture (`projects/05/`): Implement the complete Hack computer by wiring together all previously built chips. `Memory.hdl` unifies 16K RAM, an 8K screen memory map, and a keyboard register into a single address space. `CPU.hdl` decodes and executes Hack machine instructions using the ALU, A register, D register, and program counter. `Computer.hdl` connects the CPU, Memory, and a ROM32K into the top-level computer. (~3 HDL files)

Use the following HDL conventions:

HDL syntax: `CHIP Name { IN pins; OUT pins; PARTS: ChipName(pin=wire, ...); ... }`
Bus slices: `a[0]`, `a[0..7]`, `false`, `true` are valid wire values. Built-in chips available: `Nand`, `DFF`, `ARegister`, `DRegister`, `ROM32K`, `Screen`, `Keyboard`, `RAM16K` (only for Memory.hdl — you must implement your own RAM16K in projects/03/).

Use the following Hack assembly conventions:

A-instruction: `@value` — loads a 15-bit value into the A register.
C-instruction: `dest=comp;jump` — computes, optionally stores result, optionally jumps.
Labels: `(LABEL)` defines a ROM address symbol. Predefined symbols: R0-R15, SP, LCL, ARG, THIS, THAT, SCREEN (16384), KBD (24576).
