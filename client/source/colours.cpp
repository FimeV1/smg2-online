#include "colours.h"
#include "session.h"
#include "syati.h"

extern "C" void GXInvalidateTexAll();

namespace MR {
    void changeModelDataTexAll(LiveActor *pActor, const char *pTexName, const ResTIMG &rTexture);
};

namespace {
    // One entry of a model's texture table. (Syati's ResTIMG is not the full
    // 32 bytes, so it cannot be used to walk the table.)
    struct TexHeader {
        u8 format;
        u8 enableAlpha;
        u16 width;
        u16 height;
        u8 rest[0x16];
        s32 imageOffset; // from the start of this header
    };

    const u32 MAX_TEXTURES = 12;       // texture table entries that belong to the outfit
    const u32 SLOT_BYTES = 0x10000;    // recoloured image data per player
    const u8 FORMAT_CMPR = 14;

    // Hue (degrees) of the cap and shirt, and of the overalls
    struct Outfit {
        s16 capHue;
        s16 overallsHue;
    };

    // The first is what the local player wears; remote players use the rest
    const Outfit OUTFITS[] = {
        { 0, 235 },    // red, blue
        { 120, 235 },  // green, blue
        { 52, 280 },   // yellow, purple
        { 215, 0 },    // blue, red
        { 285, 30 },   // purple, orange
        { 28, 130 },   // orange, green
        { 180, 250 },  // cyan, navy
        { 325, 190 },  // pink, teal
    };
    const u32 OUTFIT_COUNT = sizeof(OUTFITS) / sizeof(OUTFITS[0]);

    struct Entry {
        u16 index;        // in the model's texture table
        u16 image;        // which of the unique images it shows
        TexHeader original;
        const u8 *pOriginalData;
    };

    Entry sEntries[MAX_TEXTURES];
    u32 sEntryCount;
    u32 sImageOffset[MAX_TEXTURES]; // of each unique image inside a slot
    u32 sImageSize[MAX_TEXTURES];
    const u8 *sImageData[MAX_TEXTURES];
    u32 sImageCount;
    J3DModelData *sModelData;

    u8 sPixels[Colours::MAX_SLOTS][SLOT_BYTES] __attribute__((aligned(32)));
    bool sPainted[Colours::MAX_SLOTS];

    inline TexHeader *getTextures(J3DModelData *pModelData, u16 *pCount) {
        // J3DModelData keeps its J3DTexture at 0x6C: count, then the table
        u8 *pTexture = *(u8 **)((u8 *)pModelData + 0x6C);
        *pCount = *(u16 *)pTexture;
        return *(TexHeader **)(pTexture + 4);
    }

    inline const JUTNameTab *getTextureNames(J3DModelData *pModelData) {
        return *(const JUTNameTab **)((u8 *)pModelData + 0x70);
    }

    inline const u8 *getImage(const TexHeader *pTexture) {
        return (const u8 *)pTexture + pTexture->imageOffset;
    }

    inline void setImage(TexHeader *pTexture, const u8 *pData) {
        pTexture->imageOffset = (s32)(pData - (const u8 *)pTexture);
    }

    bool isOutfitTexture(const char *pName, const TexHeader *pTexture) {
        return pName && pTexture->format == FORMAT_CMPR && (strstr(pName, "Body") || strstr(pName, "Cap"));
    }

    // Finds the outfit textures of this model (once per scene)
    void findTextures(LiveActor *pActor) {
        J3DModelData *pModelData = MR::getJ3DModelData(pActor);
        if (pModelData == sModelData) {
            return;
        }
        sModelData = pModelData;
        sEntryCount = 0;
        sImageCount = 0;

        u16 count;
        TexHeader *pTextures = getTextures(pModelData, &count);
        const JUTNameTab *pNames = getTextureNames(pModelData);
        u32 used = 0;

        for (u16 i = 0; i < count && sEntryCount < MAX_TEXTURES; i++) {
            TexHeader *pTexture = &pTextures[i];
            if (!isOutfitTexture(pNames->getName(i), pTexture)) {
                continue;
            }

            const u8 *pData = getImage(pTexture);
            u32 image = 0;
            for (; image < sImageCount; image++) {
                if (sImageData[image] == pData) {
                    break;
                }
            }
            if (image == sImageCount) {
                // CMPR: 4 bits per pixel, no mipmaps wanted
                u32 size = ((pTexture->width + 7) & ~7) * ((pTexture->height + 7) & ~7) / 2;
                if (used + size > SLOT_BYTES) {
                    continue;
                }
                sImageData[image] = pData;
                sImageOffset[image] = used;
                sImageSize[image] = size;
                used += (size + 31) & ~31;
                sImageCount++;
            }

            Entry &rEntry = sEntries[sEntryCount++];
            rEntry.index = i;
            rEntry.image = (u16)image;
            rEntry.original = *pTexture;
            rEntry.pOriginalData = pData;
        }
    }

    // ---- recolouring ----

    void toHsv(f32 r, f32 g, f32 b, f32 *pH, f32 *pS, f32 *pV) {
        f32 max = r > g ? (r > b ? r : b) : (g > b ? g : b);
        f32 min = r < g ? (r < b ? r : b) : (g < b ? g : b);
        f32 delta = max - min;
        *pV = max;
        *pS = max > 0.0f ? delta / max : 0.0f;
        if (delta <= 0.0f) {
            *pH = 0.0f;
        }
        else if (max == r) {
            *pH = 60.0f * ((g - b) / delta);
        }
        else if (max == g) {
            *pH = 60.0f * ((b - r) / delta) + 120.0f;
        }
        else {
            *pH = 60.0f * ((r - g) / delta) + 240.0f;
        }
        if (*pH < 0.0f) {
            *pH += 360.0f;
        }
    }

    void toRgb(f32 h, f32 s, f32 v, f32 *pR, f32 *pG, f32 *pB) {
        while (h >= 360.0f) {
            h -= 360.0f;
        }
        s32 sector = (s32)(h / 60.0f);
        f32 f = h / 60.0f - sector;
        f32 p = v * (1.0f - s);
        f32 q = v * (1.0f - s * f);
        f32 t = v * (1.0f - s * (1.0f - f));
        switch (sector) {
        case 0: *pR = v; *pG = t; *pB = p; break;
        case 1: *pR = q; *pG = v; *pB = p; break;
        case 2: *pR = p; *pG = v; *pB = t; break;
        case 3: *pR = p; *pG = q; *pB = v; break;
        case 4: *pR = t; *pG = p; *pB = v; break;
        default: *pR = v; *pG = p; *pB = q; break;
        }
    }

    // Reds become the cap colour and blues the overalls colour; skin, buttons,
    // shoes and anything grey stay as they are.
    u16 recolour(u16 colour, const Outfit &rOutfit) {
        f32 r = ((colour >> 11) & 0x1F) * (1.0f / 31.0f);
        f32 g = ((colour >> 5) & 0x3F) * (1.0f / 63.0f);
        f32 b = (colour & 0x1F) * (1.0f / 31.0f);

        f32 h, s, v;
        toHsv(r, g, b, &h, &s, &v);
        if (s < 0.35f) {
            return colour;
        }
        if (h >= 340.0f || h < 14.0f) {
            h = (f32)rOutfit.capHue + (h >= 340.0f ? h - 360.0f : h);
        }
        else if (h >= 195.0f && h < 265.0f) {
            h = (f32)rOutfit.overallsHue + (h - 235.0f);
        }
        else {
            return colour;
        }
        if (h < 0.0f) {
            h += 360.0f;
        }

        toRgb(h, s, v, &r, &g, &b);
        return (u16)(((u32)(r * 31.0f + 0.5f) << 11) | ((u32)(g * 63.0f + 0.5f) << 5) | (u32)(b * 31.0f + 0.5f));
    }

    // A CMPR texture is a list of 8-byte blocks: two RGB565 colours and 16
    // 2-bit picks between them. Only the two colours need to change, but which
    // of them is larger selects the block's mode, so their order must be kept.
    void recolourImage(u8 *pDest, const u8 *pSrc, u32 size, const Outfit &rOutfit) {
        for (u32 offset = 0; offset + 8 <= size; offset += 8) {
            const u8 *pIn = pSrc + offset;
            u8 *pOut = pDest + offset;
            u16 a = (u16)(pIn[0] << 8 | pIn[1]);
            u16 b = (u16)(pIn[2] << 8 | pIn[3]);
            u16 newA = recolour(a, rOutfit);
            u16 newB = recolour(b, rOutfit);
            u32 picks = (u32)pIn[4] << 24 | (u32)pIn[5] << 16 | (u32)pIn[6] << 8 | pIn[7];

            if (a > b) {
                // four-colour block: must stay a > b
                if (newA == newB) {
                    if (newA == 0xFFFF) {
                        newB--;
                    }
                    else {
                        newA++;
                    }
                }
                else if (newA < newB) {
                    u16 swap = newA;
                    newA = newB;
                    newB = swap;
                    picks ^= 0x55555555; // 0 <-> 1, 2 <-> 3
                }
            }
            else if (newA > newB) {
                // three-colour block (pick 3 is transparent): must stay a <= b
                u16 swap = newA;
                newA = newB;
                newB = swap;
                u32 fixed = 0;
                for (u32 i = 0; i < 32; i += 2) {
                    u32 pick = (picks >> i) & 3;
                    if (pick < 2) {
                        pick ^= 1;
                    }
                    fixed |= pick << i;
                }
                picks = fixed;
            }

            pOut[0] = (u8)(newA >> 8);
            pOut[1] = (u8)newA;
            pOut[2] = (u8)(newB >> 8);
            pOut[3] = (u8)newB;
            pOut[4] = (u8)(picks >> 24);
            pOut[5] = (u8)(picks >> 16);
            pOut[6] = (u8)(picks >> 8);
            pOut[7] = (u8)picks;
        }
    }
};

namespace Colours {
    void reset() {
        sModelData = NULL;
        sEntryCount = 0;
    }

    void setupActor(LiveActor *pActor) {
        findTextures(pActor);

        const JUTNameTab *pNames = getTextureNames(sModelData);
        for (u32 i = 0; i < sEntryCount; i++) {
            MR::initDLMakerChangeTex(pActor, pNames->getName(sEntries[i].index));
        }
        MR::newDifferedDLBuffer(pActor);

        for (u32 i = 0; i < MAX_SLOTS; i++) {
            sPainted[i] = false;
        }
    }

    void paint(u32 slot, u8 playerId, LiveActor *pActor) {
        if (slot >= MAX_SLOTS || sEntryCount == 0) {
            return;
        }

        // Everyone sees themselves in red and blue, so nobody else gets that one
        const Outfit &rOutfit = OUTFITS[Settings::playerColours ? 1 + playerId % (OUTFIT_COUNT - 1) : 0];
        for (u32 i = 0; i < sImageCount; i++) {
            recolourImage(sPixels[slot] + sImageOffset[i], sImageData[i], sImageSize[i], rOutfit);
        }
        DCFlushRange(sPixels[slot], SLOT_BYTES);
        GXInvalidateTexAll();
        sPainted[slot] = true;

        // Flags the materials so the actor rebuilds its display list. This
        // also points the shared texture table at our copy; end() undoes that.
        const JUTNameTab *pNames = getTextureNames(sModelData);
        for (u32 i = 0; i < sEntryCount; i++) {
            TexHeader texture = sEntries[i].original;
            setImage(&texture, sPixels[slot] + sImageOffset[sEntries[i].image]);
            MR::changeModelDataTexAll(pActor, pNames->getName(sEntries[i].index), *(const ResTIMG *)&texture);
        }
        end(pActor);
    }

    void begin(u32 slot, LiveActor *pActor) {
        if (slot >= MAX_SLOTS || !sPainted[slot] || MR::getJ3DModelData(pActor) != sModelData) {
            return;
        }
        u16 count;
        TexHeader *pTextures = getTextures(sModelData, &count);
        for (u32 i = 0; i < sEntryCount; i++) {
            setImage(&pTextures[sEntries[i].index], sPixels[slot] + sImageOffset[sEntries[i].image]);
        }
    }

    void end(LiveActor *pActor) {
        if (MR::getJ3DModelData(pActor) != sModelData) {
            return;
        }
        u16 count;
        TexHeader *pTextures = getTextures(sModelData, &count);
        for (u32 i = 0; i < sEntryCount; i++) {
            TexHeader *pTexture = &pTextures[sEntries[i].index];
            *pTexture = sEntries[i].original;
            setImage(pTexture, sEntries[i].pOriginalData);
        }
    }
};
