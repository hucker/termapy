# Rebuild the ELF fixtures for tests/test_symbols_elf.py from elf_sample.c.
#
# The tests read the committed .elf files; run this only when the sample
# changes.  Needs XC32 v5 with the PIC32CM-LE device pack (MPLAB X's pack
# manager puts it under ~/.mchp_packs), and zig for the Clang build.
#
#   elf_xc32_cm23.elf    GCC (XC32), DWARF 2, little-endian, 32-bit, Thumb
#   elf_nodebug.elf      the same build without -g: a symbol table, no types
#   elf_clang_ppc64.elf  Clang (zig cc), DWARF 5, big-endian, 64-bit
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$xc32 = "C:\Program Files\Microchip\xc32\v5.00\bin\xc32-gcc.exe"
$dfp = Join-Path $env:USERPROFILE ".mchp_packs\Microchip\PIC32CM-LE_DFP\1.3.280"
& $xc32 -mprocessor=PIC32CM5164LE00064 "-mdfp=$dfp" -g -O1 -o elf_xc32_cm23.elf elf_sample.c
& $xc32 -mprocessor=PIC32CM5164LE00064 "-mdfp=$dfp" -O1 -o elf_nodebug.elf elf_sample.c
zig cc -target powerpc64-freestanding-none -gdwarf-5 -O1 -ffreestanding `
    -fno-sanitize=undefined -nostdlib "-Wl,-e,main" -o elf_clang_ppc64.elf elf_sample.c
