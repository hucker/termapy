/* Fixture source for tests/test_symbols_elf.py: one global per type rule.

   Rebuild the committed .elf files with build_fixtures.ps1; the tests only
   read them.  Every global is referenced from main() so no compiler or
   linker drops one. */
#include <stdbool.h>
#include <stdint.h>

#include "elf_sample.h"

uint16_t gU16 = 0x1234;
int16_t gI16 = -2;
bool gFlag = true;
char gChar = 'A';
signed char gSChar = -3;
float gF32 = 2.5f;
double gF64 = 1.25;
uint32_t *gPtr;

enum Mode { MODE_OFF = -1, MODE_IDLE = 0, MODE_RUN = 2 };
enum Mode gMode = MODE_RUN;

enum Level { LEVEL_LOW, LEVEL_HIGH };
enum Level gLevel = LEVEL_HIGH;

const uint32_t kTable[4] = {1, 2, 3, 4};
char gName[8] = "termapy";

struct Inner {
    uint8_t a;
    uint8_t b;
};

struct Config {
    uint16_t baud_div;
    int8_t trim;
    uint8_t mode;
    float gain;
    struct Inner inner;
    unsigned int ready : 1;
    unsigned int level : 3;
};

struct Config gConfig = {0x0102, -4, 3, 0.5f, {5, 6}, 1, 5};

uint32_t gDeclared = 7;

/* Constants: the build holds their values, so termapy reads them.  A
   pointer constant to a register block is how firmware says what a
   peripheral is used for; termapy turns it into an alias once a device
   file places a block at that address (SPI_PORT -> SPI_PORT_DIR ...).  A
   pointer to one word is a value, not an alias.  The address is the
   PIC32CM's PORT group 2 (PORTC); the ppc64 build only has to carry it. */
struct PortGroup {
    uint32_t DIR;
    uint32_t DIRCLR;
    uint32_t OUT;
    uint32_t IN;
};

volatile struct PortGroup *const SPI_PORT = (volatile struct PortGroup *)0x41008080UL;
volatile uint32_t *const SPI_DIR = (volatile uint32_t *)0x41008080UL;
const uint8_t kSpiSckPin = 4;
const int16_t kTrim = -7;
const float kGain = 0.1f;
const enum Level kLevel = LEVEL_HIGH;

uint32_t tick(void) {
    static uint32_t count;
    return ++count;
}

volatile uintptr_t gSink;

int main(void) {
    gPtr = &gDeclared;
    gSink = (uintptr_t)&gU16 + (uintptr_t)&gI16 + (uintptr_t)&gFlag + (uintptr_t)&gChar
            + (uintptr_t)&gSChar + (uintptr_t)&gF32 + (uintptr_t)&gF64 + (uintptr_t)&gMode
            + (uintptr_t)&gLevel + (uintptr_t)kTable + (uintptr_t)gName + (uintptr_t)&gConfig
            + (uintptr_t)SPI_PORT + (uintptr_t)SPI_DIR + (uintptr_t)&kSpiSckPin
            + (uintptr_t)&kTrim + (uintptr_t)&kGain + (uintptr_t)&kLevel;
    return (int)tick();
}
