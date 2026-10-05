#include "session.h"
#include "syati.h"

// The title screen: "ONLINE" is painted into the game's logo. It is grey
// until the server has answered, then lights up.
//
// Nothing is replaced on the disc. The logo texture is edited in memory after
// the game has loaded it, so this works the same on Dolphin and on a console.

#if defined(SB4E)
#define ADDR_TITLE_CALL_LOGO_CTOR 0x804A4E48
#endif

class TitleLogoLayout;

extern "C" {
    TitleLogoLayout *__ct__Q223TitleSequenceProductSub10LogoLayoutFv(TitleLogoLayout *);
    void GXInvalidateTexAll();
}

namespace MR {
    // Returns a nw4r::lyt::TexMap
    void *getLytTexMap(LayoutActor *pLayout, const char *pPaneName, u8 index);
};

namespace {
    const u32 LOGO_WIDTH = 440;
    const u32 LOGO_HEIGHT = 216;
    const u32 STATUS_INTERVAL = 30;

    // The word sits in the empty corner left of "SUPER" (the Luma covers the right one)
    const s32 TEXT_X = 5;
    const s32 TEXT_Y = 18;
    const s32 SCALE = 3;
    const s32 OUTLINE = 2;
    const s32 GLYPH_W = 5;
    const s32 GLYPH_H = 7;
    const s32 GLYPH_STEP = GLYPH_W + 1;

    // 5x7, one row per byte, most significant of the 5 bits on the left
    const u8 GLYPHS[][GLYPH_H] = {
        { 0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E }, // O
        { 0x11, 0x19, 0x15, 0x13, 0x11, 0x11, 0x11 }, // N
        { 0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F }, // L
        { 0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x1F }, // I
        { 0x11, 0x19, 0x15, 0x13, 0x11, 0x11, 0x11 }, // N
        { 0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F }, // E
    };
    const s32 GLYPH_COUNT = sizeof(GLYPHS) / sizeof(GLYPHS[0]);
    const s32 TEXT_W = (GLYPH_COUNT * GLYPH_STEP - 1) * SCALE;
    const s32 TEXT_H = GLYPH_H * SCALE;

    LayoutActor *sLogo;
    bool sIsLit;

    // Is the pixel (x, y), relative to the word, part of a letter?
    bool isInk(s32 x, s32 y) {
        if (x < 0 || y < 0 || x >= TEXT_W || y >= TEXT_H) {
            return false;
        }
        s32 column = x / SCALE;
        s32 glyph = column / GLYPH_STEP;
        s32 bit = column % GLYPH_STEP;
        if (bit >= GLYPH_W) {
            return false;
        }
        return (GLYPHS[glyph][y / SCALE] >> (GLYPH_W - 1 - bit)) & 1;
    }

    bool isNearInk(s32 x, s32 y, s32 distance) {
        for (s32 dy = -distance; dy <= distance; dy++) {
            for (s32 dx = -distance; dx <= distance; dx++) {
                if (isInk(x + dx, y + dy)) {
                    return true;
                }
            }
        }
        return false;
    }

    // RGB5A3 stores 4x4 blocks of 16-bit pixels; the top bit means opaque RGB555
    inline u16 *pixelAt(u8 *pImage, s32 x, s32 y) {
        u32 block = (y / 4) * (LOGO_WIDTH / 4) + (x / 4);
        return (u16 *)(pImage + (block * 16 + (y % 4) * 4 + (x % 4)) * 2);
    }

    inline u16 opaque(u32 r, u32 g, u32 b) {
        return (u16)(0x8000 | (r >> 3) << 10 | (g >> 3) << 5 | (b >> 3));
    }

    void paintLogo(LayoutActor *pLayout, bool isLit) {
        // nw4r::lyt::TexMap starts with: image, palette, u16 width, u16 height
        u8 *pTexMap = (u8 *)MR::getLytTexMap(pLayout, "PicLogo", 0);
        if (!pTexMap) {
            return;
        }
        u8 *pImage = *(u8 **)pTexMap;
        if (!pImage || *(u16 *)(pTexMap + 8) != LOGO_WIDTH || *(u16 *)(pTexMap + 10) != LOGO_HEIGHT) {
            return; // not the logo this was drawn for (another language's, say)
        }

        const s32 margin = OUTLINE + 1;
        for (s32 y = -margin; y < TEXT_H + margin + 2; y++) {
            for (s32 x = -margin; x < TEXT_W + margin + 2; x++) {
                u16 colour;
                if (isInk(x, y)) {
                    u32 shade = (u32)y * 255 / (TEXT_H - 1);
                    if (isLit) {
                        // white at the top to sky blue at the bottom, like "SUPER"
                        colour = opaque(255 - shade * 110 / 255, 255 - shade * 40 / 255, 255);
                    }
                    else {
                        u32 grey = 150 - shade * 50 / 255;
                        colour = opaque(grey, grey, grey);
                    }
                }
                else if (isNearInk(x, y, OUTLINE)) {
                    colour = opaque(10, 30, 120);
                }
                else if (isNearInk(x - 2, y - 2, OUTLINE)) {
                    colour = opaque(0, 8, 40); // drop shadow
                }
                else {
                    continue;
                }
                *pixelAt(pImage, TEXT_X + x, TEXT_Y + y) = colour;
            }
        }

        DCFlushRange(pImage, LOGO_WIDTH * LOGO_HEIGHT * 2);
        GXInvalidateTexAll();
    }

    TitleLogoLayout *onLogoCreated(TitleLogoLayout *pLayout) {
        __ct__Q223TitleSequenceProductSub10LogoLayoutFv(pLayout);
        sLogo = (LayoutActor *)pLayout;
        sIsLit = Session::isConnected();
        paintLogo(sLogo, sIsLit);
        return pLayout;
    }

    kmCall(ADDR_TITLE_CALL_LOGO_CTOR, onLogoCreated);
};

namespace Title {
    void update() {
        if (!sLogo || MR::isDead(sLogo) || Session::getFrame() % STATUS_INTERVAL != 0) {
            return;
        }
        if (Session::isConnected() != sIsLit) {
            sIsLit = !sIsLit;
            paintLogo(sLogo, sIsLit);
        }
    }
};
