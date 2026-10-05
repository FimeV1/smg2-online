#pragma once

#include "revolution.h"

class LiveActor;

// Gives each remote player their own outfit colours.
//
// Every player actor shares one model, and with it one set of textures. The
// game can still draw actors of one model with different textures (the story
// book pages work that way): an actor flagged for it keeps a private display
// list that remembers whichever texture was current when the list was built.
// So each remote player gets a recoloured copy of the outfit textures, which
// is switched in only while that actor builds its display list.
namespace Colours {
    const u32 MAX_SLOTS = 7;

    // A new scene: forget the model of the last one
    void reset();

    // In the actor's init, after its model exists
    void setupActor(LiveActor *pActor);

    // Recolour slot `slot` for player `playerId`; the actor redraws with it
    void paint(u32 slot, u8 playerId, LiveActor *pActor);

    // Around the actor's calcAnim
    void begin(u32 slot, LiveActor *pActor);
    void end(LiveActor *pActor);
};
